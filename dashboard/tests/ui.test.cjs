"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const helperContext = {module: {exports: {}}, Date, Number, String, Set, Map, URLSearchParams};
vm.runInNewContext(source, helperContext);
const helpers = helperContext.module.exports;
const NOW = 1_800_000_000_000;

/** Minimal DOM simulation: no browser, external network, or controller calls. */
function observerHarness(options = {}) {
  let now = NOW;
  const images = [];
  const elements = new Map();
  const timers = [];
  const replacedUrls = [];
  const stored = new Map();
  const frameCallbacks = [];
  if (options.token) stored.set("holospeed.operator", options.token);
  class Element {
    constructor() {
      this.hidden = false;
      this.dataset = {};
      this.children = new Map();
      this.textContent = "";
      this.value = "all";
      this.scrollTop = 0;
      const classes = new Set();
      this.classList = {
        add(value) {classes.add(value);},
        toggle(value, enabled) {if (enabled) classes.add(value); else classes.delete(value);},
        contains(value) {return classes.has(value);},
      };
    }
    querySelector(selector) {
      if (!this.children.has(selector)) this.children.set(selector, new Element());
      return this.children.get(selector);
    }
    addEventListener() {}
    setAttribute() {}
    append(...children) {this.appended = [...(this.appended || []), ...children];}
    replaceChildren(...children) {
      this.nodes = children;
      this.textContent = children.map((child) => typeof child === "string" ? child : child.textContent || "").join("");
    }
  }
  function element(id) {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  }
  element("game-frame").hidden = true;
  const video = element("game-video");
  video.hidden = true;
  video.paused = false;
  video.readyState = 2;
  video.currentTime = 1;
  video.seekable = {length: 1, end: () => 2};
  video.play = async () => {video.paused = false;};
  video.requestVideoFrameCallback = (callback) => frameCallbacks.push(callback);
  const forwardKey = new Element();
  forwardKey.dataset.key = "forward";
  const context = {
    document: {
      getElementById: element,
      querySelectorAll: (selector) => selector === "[data-key]" ? [forwardKey] : selector === "[data-milestone]" ? [...(element("preparation-milestones").nodes || []), ...(element("milestones").nodes || [])] : [],
      createElement: () => new Element(),
      createTextNode: (textContent) => ({textContent}),
      createDocumentFragment: () => new Element(),
    },
    Image: class {
      constructor() {images.push(this);}
      set src(value) {this.url = value;}
      get src() {return this.url;}
    },
    Date: class extends Date {static now() {return now;}},
    setInterval() {}, setTimeout(callback, delay) {timers.push({callback, delay});}, clearTimeout() {},
    Number, String, Set, Map, encodeURIComponent, URLSearchParams, AbortController,
    crypto: {randomUUID: options.randomUUID || (() => "ad67598a-6d1f-4e66-8730-0e2e76c1cd28")},
    Hls: options.Hls,
    HoloVoice: options.HoloVoice,
    location: {hash: options.hash || "", pathname: "/", search: ""},
    history: {replaceState(_state, _title, url) {replacedUrls.push(url);}},
    sessionStorage: {getItem(key) {return stored.get(key) || null;}, setItem(key, value) {stored.set(key, value);}, removeItem(key) {stored.delete(key);}},
    fetch: options.fetch || (() => {throw new Error("Regression harness must not issue network requests");}),
  };
  const entryPoint = /\n  poll\(\);\n\}\)\(\);\s*$/;
  assert.match(source, entryPoint, "UI startup hook must be present");
  const instrumented = source.replace(entryPoint, `
    globalThis.observerTest = {
      set(value, online = true) {envelope = value; reachable = online;},
      updateCapture, renderState, renderControls, submitCommand, refreshCommand, lockControls, pollCapture, renderVideo, trackVideoFrame,
      renderMission, renderEngineering, handleEngineeringCommand, openEngineering, selectEngineeringRun, renderMilestones, unlockControls, renderLogin, renderLaunch,
      queueVoice, sendNextVoice, sendTypedVoice, toggleVoice, renderVoice,
      command(value) {operatorCommand = value;},
      engineering(runId, value) {engineeringRun = runId; if (value) engineeringByRun.set(runId, value);},
      journal(value, runId) {journal = value; selectedRun = runId;},
      videoMetadata(value) {videoMetadata = value; videoReachable = true; startVideo(value);},
      resources(state, fresh = true) {renderResources(state, {fresh});}
    };
  })();`);
  vm.runInNewContext(instrumented, context);
  return {...context.observerTest, element, images, forwardKey, timers, replacedUrls, stored, frameCallbacks, setNow(value) {now = value;}};
}

test("formats recorded values without inventing missing measurements", () => {
  assert.equal(helpers.timestamp(NOW), NOW);
  assert.equal(helpers.timestamp(NOW / 1000), NOW);
  assert.equal(helpers.timestamp("invalid"), null);
  assert.equal(helpers.elapsed(61234, true), "01:01.2");
  assert.equal(helpers.latency(null), "—");
  assert.equal(helpers.latency(5037), "5.04 s");
  assert.equal(helpers.ageLabel(null), "No capture received");
});

test("separates capture freshness, telemetry freshness, and displayed pixel age", () => {
  assert.equal(helpers.captureState({captured_at: NOW - 1000, feed_status: "online"}, NOW).fresh, true);
  assert.equal(helpers.captureState({captured_at: NOW - 20000, feed_status: "online"}, NOW).fresh, false);
  assert.equal(helpers.captureState({captured_at: NOW - 1000, feed_status: "stale"}, NOW).fresh, false);
  assert.equal(helpers.telemetryState({updated_at: NOW - 30000, feed_status: "online"}, NOW).label, "Telemetry stale");
  assert.equal(helpers.telemetryState({updated_at: NOW - 1000, feed_status: "controller-offline"}, NOW).fresh, false);
  assert.equal(helpers.displayedFrameState(NOW - 60000, NOW).fresh, false);
  assert.equal(helpers.displayedFrameState(NOW - 1000, NOW).fresh, true);
  assert.equal(helpers.captureState({captured_at: NOW + 5000, feed_status: "online"}, NOW).fresh, false);
  assert.equal(helpers.telemetryState({updated_at: NOW + 5000, feed_status: "online"}, NOW).fresh, false);
  assert.equal(helpers.displayedFrameState(NOW + 5000, NOW).fresh, false);
});

test("does not starve slow images when capture metadata advances", () => {
  const ui = observerHarness();
  ui.set({capture_seq: 1, captured_at: NOW, updated_at: NOW, feed_status: "online"});
  ui.updateCapture();
  assert.equal(ui.images.length, 1);
  ui.set({capture_seq: 2, captured_at: NOW + 1000, updated_at: NOW + 1000, feed_status: "online"});
  ui.updateCapture();
  assert.equal(ui.images.length, 1, "Exactly one JPEG request can be in flight");
  ui.images[0].onload();
  assert.equal(ui.element("game-frame").src, "/api/frame.jpg?seq=1");
  assert.equal(ui.element("game-frame").hidden, false);
  assert.equal(ui.images.length, 2, "A completed older capture starts the latest capture next");
  assert.equal(ui.images[1].src, "/api/frame.jpg?seq=2");
});

test("fresh metadata cannot label old displayed pixels live after image failure", () => {
  const ui = observerHarness();
  ui.set({capture_seq: 1, captured_at: NOW, updated_at: NOW, feed_status: "online"});
  ui.updateCapture(); ui.images[0].onload();
  ui.setNow(NOW + 30000);
  ui.set({capture_seq: 2, captured_at: NOW + 30000, updated_at: NOW + 30000, feed_status: "online"});
  ui.updateCapture(); ui.images[1].onerror(); ui.updateCapture();
  assert.equal(ui.element("feed-status").querySelector("span").textContent, "Stale frame");
  assert.equal(ui.element("frame-notice").hidden, false);
  assert.equal(ui.element("capture-age").textContent, "Captured 30s ago");
});

test("respects explicit offline status even while the displayed frame is recent", () => {
  const ui = observerHarness();
  ui.set({capture_seq: 1, captured_at: NOW, updated_at: NOW, feed_status: "online"});
  ui.updateCapture(); ui.images[0].onload();
  ui.setNow(NOW + 5000);
  ui.set({capture_seq: 1, captured_at: NOW, updated_at: NOW, feed_status: "offline"});
  ui.updateCapture();
  assert.equal(ui.element("feed-status").querySelector("span").textContent, "Stale frame");
  assert.equal(ui.element("frame-notice").hidden, false);
});

test("can show an initial archived capture with an explicit stale indicator", () => {
  const ui = observerHarness();
  ui.set({capture_seq: 1, captured_at: NOW - 60000, updated_at: NOW - 60000, feed_status: "stale"});
  ui.updateCapture();
  assert.equal(ui.images.length, 1);
  ui.images[0].onload();
  assert.equal(ui.element("game-frame").hidden, false);
  assert.equal(ui.element("feed-status").querySelector("span").textContent, "Stale frame");
  assert.equal(ui.element("frame-notice").hidden, false);
});

test("stale published telemetry cannot leave active input keys highlighted", () => {
  const ui = observerHarness();
  const state = {status: "running", keys: ["forward"], action: {keys: ["forward"], duration_ticks: 6}};
  ui.set({updated_at: NOW, feed_status: "online", state, runs: []});
  ui.renderState();
  assert.equal(ui.forwardKey.classList.contains("active"), true);
  assert.equal(ui.element("agent-status").textContent, "Agent running");
  ui.setNow(NOW + 30000); ui.renderState();
  assert.equal(ui.forwardKey.classList.contains("active"), false);
  assert.equal(ui.element("agent-status").textContent, "Telemetry stale");
  assert.equal(ui.element("input-caption").textContent, "Unavailable");
});

test("merges snapshot and archived events by sequence and preserves claim language", () => {
  const events = helpers.mergeEvents(
    [{seq: 2, elapsed_ms: 20, type: "action", data: {}}],
    [{seq: 1, elapsed_ms: 10, type: "plan", data: {}}, {seq: 2, elapsed_ms: 20, type: "action", data: {updated: true}}],
  );
  assert.equal(JSON.stringify(events.map((event) => event.seq)), "[1,2]");
  assert.equal(events[1].data.updated, true);
  assert.equal(helpers.eventDescription({type: "plan", data: {provider: "holo", decision: {objective: "Collect visible logs"}}}), "Holo4 · Collect visible logs · awaiting validation");
  assert.equal(helpers.eventDescription({type: "inference_metrics", data: {provider: "jev", latency_ms: 250, usage: {input_tokens: 12, output_tokens: 3}}}), "Jev · 250 ms · 12 in / 3 out");
  assert.match(helpers.eventDescription({type: "milestone", data: {id: "wood", evidence: "Model reports a log"}}), /^Visual claim:/);
});

test("all literal UI element references resolve to unique HTML IDs", () => {
  const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(ids).size, ids.length);
  for (const match of source.matchAll(/\$\("([^"\n]+)"\)/g)) assert.ok(ids.includes(match[1]), `Missing element ${match[1]}`);
});

test("milestones retain distinct game-state, simulated, and model evidence labels", () => {
  assert.equal(helpers.milestoneLabel({verification: "game_state"}, "live"), "Confirmed from game state");
  assert.equal(helpers.milestoneLabel({verification: "simulated"}, "live"), "Simulated");
  assert.equal(helpers.milestoneLabel({}, "demo"), "Simulated");
  assert.equal(helpers.milestoneLabel({verification: "model_visual_claim"}, "live"), "Visual claim");
  assert.equal(helpers.milestoneLabel({}, "live"), "Visual claim");
  assert.match(helpers.eventDescription({type: "milestone", data: {id: "wood", verification: "game_state", evidence: "Inventory contains birch_log"}}), /^Confirmed from game state: Gather wood/);
  assert.equal(helpers.eventDescription({type: "milestone", data: {id: "wood", verification: "game_state", evidence: {inventory: {"minecraft:birch_log": 4}}}}), "Confirmed from game state: Gather wood · birch log × 4");
  assert.equal(helpers.eventDescription({type: "milestone", data: {id: "nether", verification: "game_state", evidence: {dimension: "minecraft:the_nether"}}}), 'Confirmed from game state: Nether entry · {"dimension":"minecraft:the_nether"}');
});

test("skill progress belongs to the current item, target, and skill attempt", () => {
  const action = {skill: "mine", item: "minecraft:birch_log", target_count: 4};
  const progress = {seq: 9, elapsed_ms: 1000, type: "skill_progress", data: {
    inventory_count: 2, skill: {kind: "mine", item: "minecraft:birch_log", target_count: 4, phase: "mining"},
  }};
  assert.equal(helpers.skillTelemetry(action, [progress]).count, 2);
  assert.equal(helpers.skillTelemetry(action, [progress]).item, "birch log");
  assert.equal(helpers.skillTelemetry({...action, target_count: 6}, [progress]).count, null);
  assert.equal(helpers.skillTelemetry({...action, item: "minecraft:stone"}, [progress]).count, null);
  assert.equal(helpers.skillTelemetry(action, [progress, {seq: 10, type: "skill_started"}]).count, null);
  assert.equal(helpers.skillTelemetry(action, [{...progress, seq: 11}, {seq: 10, type: "skill_started"}]).count, 2);
  assert.equal(helpers.skillTelemetry(null, [progress]), null);
});

test("resource samples survive a recent-event window but never cross run IDs", () => {
  const ui = observerHarness();
  const sample = {seq: 2, elapsed_ms: 12300, type: "state_summary", data: {
    inventory: {"minecraft:birch_log": 3, "minecraft:stick": 0},
    player: {health: 0, hunger: 16, position: {x: -12.25, y: 64, z: 3.2}}, tick: 300,
  }};
  const action = {skill: "mine", item: "minecraft:birch_log", target_count: 4};
  ui.resources({run_id: "current", status: "running", action, events: [sample]});
  assert.equal(ui.element("player-health").textContent, "0 / 20", "Zero health is evidence, not missing data");
  assert.equal(ui.element("player-hunger").textContent, "16 / 20");
  assert.equal(ui.element("player-position").textContent, "-12.3 / 64.0 / 3.2");
  assert.equal(ui.element("inventory-items").textContent, "birch log · 3");
  assert.equal(ui.element("skill-state").textContent, "Current skill");
  assert.equal(ui.element("skill-count").textContent, "— / 4");
  ui.resources({run_id: "current", status: "running", action, events: []}, false);
  assert.equal(ui.element("resource-stamp").textContent, "Last recorded 00:12 · tick 300");
  assert.equal(ui.element("skill-state").textContent, "Last reported skill");
  assert.equal(ui.element("inventory-items").textContent, "birch log · 3");
  ui.resources({run_id: "historical-without-structured-state", events: []});
  assert.equal(ui.element("inventory-items").textContent, "");
  assert.equal(ui.element("player-health").textContent, "—");
  assert.equal(ui.element("inventory-note").textContent, "Inventory not recorded for this run.");
});

test("structured inventory distinguishes an empty sample from missing state and uses plain text", () => {
  const ui = observerHarness();
  ui.resources({run_id: "a", events: [{seq: 1, type: "state_summary", data: {inventory: {}}}]});
  assert.equal(ui.element("inventory-note").textContent, "Inventory empty at this sample.");
  ui.resources({run_id: "a", events: [{seq: 2, type: "state_summary", data: {inventory: {"<img onerror=bad>": 1}}}]});
  assert.equal(ui.element("inventory-items").textContent, "<img onerror=bad> · 1");
  assert.match(helpers.eventDescription({type: "skill_progress", data: {inventory_count: 2, skill: {item: "minecraft:birch_log", target_count: 4, phase: "mining"}}}), /birch log · 2 \/ 4 · mining/);
});

test("operator fragment is removed before requests and kept only in session storage", () => {
  const token = "test-operator-capability-with-at-least-32-characters";
  const ui = observerHarness({hash: `#control=${token}`});
  assert.deepEqual(ui.replacedUrls, ["/"]);
  assert.equal(ui.stored.get("holospeed.operator"), token);
  ui.set({updated_at: NOW, feed_status: "online", state: {status: "paused", connected: true, run_id: "run-1"}});
  ui.renderControls();
  assert.equal(ui.element("operator-status").textContent, "Operator access");
  assert.equal(ui.element("control-resume").disabled, false);
  assert.equal(ui.element("control-pause").disabled, true);
  assert.ok(!ui.element("control-feedback").textContent.includes(token));
  ui.lockControls();
  assert.equal(ui.stored.has("holospeed.operator"), false);
  assert.equal(ui.element("control-resume").disabled, true);
});

test("public, stale, disconnected, and pending states cannot issue unintended controls", () => {
  const running = {status: "running", connected: true, run_id: "run-1"};
  for (const values of [[true, false, false], [false, true, false], [true, true, true]]) {
    const availability = helpers.controlAvailability(running, ...values);
    assert.equal(Object.values(availability).some(Boolean), false);
  }
  assert.equal(helpers.controlAvailability(running, true, true, false).pause, true);
  assert.equal(helpers.controlAvailability(running, true, true, false).resume, false);
  assert.equal(helpers.controlAvailability({...running, connected: false}, true, true, false).start_new, false);
  const ui = observerHarness({token: "valid-stored-operator-capability-32-characters", hash: "#control=invalid"});
  ui.renderControls();
  assert.equal(ui.stored.has("holospeed.operator"), false);
  assert.equal(ui.element("operator-status").textContent, "View only");
  assert.equal(ui.element("control-start_new").disabled, true);
});

test("operator commands send only UUID/action to the same-origin queue with bearer authorization", async () => {
  const token = "test-operator-capability-with-at-least-32-characters";
  const requests = [];
  const ui = observerHarness({token, fetch: async (url, options) => {
    requests.push({url, options});
    return {ok: true, json: async () => ({command: {id: "ad67598a-6d1f-4e66-8730-0e2e76c1cd28", status: "completed", detail: "Run resumed."}})};
  }});
  ui.set({updated_at: NOW, feed_status: "online", state: {status: "paused", connected: true, run_id: "run-1"}});
  ui.renderControls();
  await ui.submitCommand("resume");
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/commands");
  assert.equal(requests[0].options.headers.Authorization, `Bearer ${token}`);
  assert.deepEqual(JSON.parse(requests[0].options.body), {id: "ad67598a-6d1f-4e66-8730-0e2e76c1cd28", action: "resume"});
  assert.equal(ui.element("control-feedback").textContent, "Run resumed.");
  assert.ok(!requests[0].options.body.includes(token));
});

test("JPEG fallback polls once per second and preserves one image in flight", async () => {
  let sequence = 1;
  const requests = [];
  const ui = observerHarness({fetch: async (url) => {
    requests.push(url);
    return {ok: true, json: async () => ({capture_seq: sequence, captured_at: NOW, updated_at: NOW, feed_status: "online"})};
  }});
  await ui.pollCapture();
  assert.equal(ui.timers.at(-1).delay, 1000);
  assert.equal(ui.images.length, 1);
  sequence = 2;
  await ui.pollCapture();
  assert.equal(ui.images.length, 1);
  ui.images[0].onload();
  assert.equal(ui.images.length, 2);
  assert.equal(ui.images[1].src, "/api/frame.jpg?seq=2");
  assert.deepEqual(requests, ["/api/capture", "/api/capture"]);
});

test("video freshness requires advancing playback, not only recent upload metadata", () => {
  const metadata = {status: "live", captured_at: NOW};
  const playback = {reachable: true, paused: false, last_frame_at: NOW - 20, playing_at: NOW - 1200, behind_live_seconds: 1};
  assert.equal(helpers.videoPlaybackState(metadata, playback, NOW).fresh, true);
  assert.equal(helpers.videoPlaybackState(metadata, {...playback, paused: true}, NOW).fresh, false);
  assert.equal(helpers.videoPlaybackState(metadata, {...playback, last_frame_at: NOW - 2000}, NOW).fresh, false);
  assert.equal(helpers.videoPlaybackState(metadata, {...playback, playing_at: NOW - 30000}, NOW).fresh, false);
  assert.equal(helpers.videoPlaybackState(metadata, {...playback, playing_at: null, behind_live_seconds: 30}, NOW).fresh, false);
  assert.equal(helpers.videoPlaybackState(metadata, {...playback, playing_at: null, behind_live_seconds: null}, NOW).fresh, false);
  assert.equal(helpers.videoPlaybackState({...metadata, status: "stale"}, playback, NOW).fresh, false);
  assert.equal(helpers.videoPlaybackState(metadata, {...playback, reachable: false}, NOW).fresh, false);
  assert.equal(helpers.videoPlaybackState(metadata, {...playback, playing_at: NOW + 3000}, NOW).fresh, false);
});

test("HLS video stays composited while starting and reports measured presented FPS", () => {
  const players = [];
  class Hls {
    static isSupported() {return true;}
    static Events = {MANIFEST_PARSED: "manifest", ERROR: "error"};
    constructor() {players.push(this);}
    on() {}
    loadSource(url) {this.url = url;}
    attachMedia(video) {this.video = video;}
    destroy() {this.destroyed = true;}
    get playingDate() {return new Date(NOW - 1000);}
  }
  const ui = observerHarness({Hls});
  const metadata = {status: "live", generation: "a", captured_at: NOW, playlist_url: "/api/live/index.m3u8", target_fps: 60};
  ui.videoMetadata(metadata); ui.renderVideo();
  assert.equal(players[0].url, "/api/live/index.m3u8");
  assert.equal(ui.element("game-video").hidden, false, "A ready playing video must remain composited so frame callbacks can begin");
  assert.equal(ui.element("feed-status").querySelector("span").textContent, "Video delayed", "Metadata alone is not live playback");
  ui.trackVideoFrame(0, {mediaTime: 1, presentedFrames: 1});
  ui.setNow(NOW + 1000);
  ui.trackVideoFrame(1000, {mediaTime: 2, presentedFrames: 61}); ui.renderVideo();
  assert.equal(ui.element("feed-status").querySelector("span").textContent, "Live video");
  assert.equal(ui.element("video-performance").textContent, "60.0 FPS / 60 target · ≈2.0s behind");
  ui.setNow(NOW + 3000); ui.renderVideo();
  assert.equal(ui.element("feed-status").querySelector("span").textContent, "Video delayed");
  ui.videoMetadata({...metadata, generation: "b"});
  assert.equal(players[0].destroyed, true);
  assert.equal(players.length, 2, "A new encoder generation must reload its media timeline");
});

test("uncertain operator submission is checked by ID and never posted again automatically", async () => {
  const requests = [];
  const ui = observerHarness({token: "test-operator-capability-with-at-least-32-characters", fetch: async (url, options) => {
    requests.push({url, method: options.method});
    if (options.method === "POST") throw new Error("Network interrupted after dispatch");
    return {ok: true, json: async () => ({command: {id: "ad67598a-6d1f-4e66-8730-0e2e76c1cd28", status: "completed", detail: "Paused."}})};
  }});
  ui.set({updated_at: NOW, feed_status: "online", state: {status: "running", connected: true, run_id: "run-1"}});
  ui.renderControls(); await ui.submitCommand("pause");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(requests.map((request) => request.method), ["POST", "GET"]);
  assert.equal(requests[1].url, "/api/commands/ad67598a-6d1f-4e66-8730-0e2e76c1cd28");
  assert.equal(ui.element("control-feedback").textContent, "Paused.");
});

test("locking controls during an in-flight POST ignores its late feedback", async () => {
  let complete;
  const ui = observerHarness({token: "test-operator-capability-with-at-least-32-characters", fetch: () => new Promise((resolve) => {complete = resolve;})});
  ui.set({updated_at: NOW, feed_status: "online", state: {status: "running", connected: true, run_id: "run-1"}});
  ui.renderControls();
  const pending = ui.submitCommand("pause");
  ui.lockControls("Controls locked by the operator.");
  complete({ok: true, json: async () => ({command: {id: "ad67598a-6d1f-4e66-8730-0e2e76c1cd28", status: "completed", detail: "Paused."}})});
  await pending;
  assert.equal(ui.element("operator-status").textContent, "View only");
  assert.equal(ui.element("control-feedback").textContent, "Controls locked by the operator.");
  assert.equal(ui.element("control-pause").disabled, true);
  assert.equal(ui.stored.has("holospeed.operator"), false);
});

test("the default route names every from-scratch checkpoint and preserves legacy event labels", () => {
  const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
  const ids = [...html.matchAll(/data-milestone="([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(ids, ["wood", "crafting_table", "wooden_pickaxe", "stone_pickaxe", "furnace", "iron_mined", "iron_smelted", "bucket", "flint_and_steel", "water_collected", "lava_located", "portal_built", "portal_lit", "nether"]);
  for (const [id, label] of Object.entries({portal_located: "Locate the portal", chest_looted: "Loot the portal chest", obsidian_acquired: "Acquire obsidian", ignition_acquired: "Acquire ignition", portal_repaired: "Repair the portal", portal_lit: "Light the portal"})) {
    assert.equal(helpers.eventDescription({type: "milestone", data: {id, verification: "game_state"}}), `Confirmed from game state: ${label}`);
  }
});

function inference(seq, provider, phase, data = {}) {
  return {seq, timestamp: new Date(NOW).toISOString(), type: "inference", data: {provider, phase, request_id: `request-${seq}`, task: "Choose the next route", input_source: provider === "holo" ? "screenshot_and_state" : "state", ...data}};
}

test("model animation requires an actual fresh pending request and stops on terminal events", () => {
  const thinking = inference(1, "holo", "thinking");
  const running = {status: "running"};
  assert.equal(helpers.inferenceStage([thinking], "holo", running, true, NOW).active, true);
  assert.equal(helpers.inferenceStage([thinking], "jev", running, true, NOW).status, "Standby");
  assert.equal(helpers.inferenceStage([thinking], "holo", running, false, NOW).active, false);
  assert.equal(helpers.inferenceStage([thinking], "holo", {status: "paused"}, true, NOW).active, false);
  assert.equal(helpers.inferenceStage([thinking], "holo", running, true, NOW + 16000).active, false);
  assert.equal(helpers.inferenceStage([thinking], "holo", running, true, NOW + 16000).status, "No result recorded");
  assert.equal(helpers.inferenceStage([thinking], "holo", {status: "paused"}, true, NOW).status, "No result recorded");
  assert.equal(helpers.inferenceStage([{...thinking, timestamp: undefined}], "holo", running, true, NOW).active, false);
  const completed = inference(2, "holo", "complete", {rationale: "The portal is nearby; prepare ignition first."});
  const stage = helpers.inferenceStage([completed, thinking], "holo", running, true, NOW);
  assert.equal(stage.active, false);
  assert.equal(stage.rationale, "The portal is nearby; prepare ignition first.");
  assert.equal(stage.status, "Decision recorded");
  const cancelled = inference(3, "holo", "failed", {cancelled: true, reason: "Agent paused"});
  assert.equal(helpers.inferenceStage([thinking, completed, cancelled], "holo", running, true, NOW).status, "Cancelled");
  const local = {seq: 4, type: "skill_decision", data: {provider: "local", reason: "Fallback"}};
  assert.equal(helpers.inferenceStage([local], "jev", running, true, NOW).status, "Standby");
});

test("mission keeps real recorded decisions across event windows without mixing runs or archives", () => {
  const ui = observerHarness();
  const state = {run_id: "live", status: "running", events: [inference(1, "holo", "complete", {rationale: "Reach the visible portal."}), inference(2, "jev", "thinking")]};
  ui.renderMission(state, {fresh: true});
  assert.equal(ui.element("mission-flow-state").textContent, "Jev is checking");
  assert.equal(ui.element("mission-jev").classList.contains("active"), true);
  assert.equal(ui.element("mission-holo-input").textContent, "Vision + game state");
  assert.equal(ui.element("mission-jev-input").textContent, "Game state");
  ui.journal({events: [inference(100, "holo", "complete", {rationale: "Archived and unrelated"})]}, "archive");
  ui.renderMission({...state, events: []}, {fresh: true});
  assert.equal(ui.element("mission-holo-rationale").textContent, "Reach the visible portal.");
  ui.renderMission({...state, events: [inference(3, "jev", "failed", {cancelled: true, reason: "Paused before dispatch"})], status: "paused"}, {fresh: true});
  assert.equal(ui.element("mission-jev").classList.contains("active"), false);
  assert.equal(ui.element("mission-jev-error").textContent, "Paused before dispatch");
  assert.equal(ui.element("mission-flow-state").textContent, "Agent paused");
  ui.renderMission({run_id: "new", status: "running", events: []}, {fresh: true});
  assert.equal(ui.element("mission-holo-rationale").textContent, "No decision recorded yet.");
  assert.equal(ui.element("mission-holo-status").textContent, "Standby");
});

test("Devin preview is bound to one run, exact digest, and the capped budget", () => {
  const preview = {run_id: "run-1", prompt: "Analyze this exact brief.\nKeep newlines.", digest_hash: "a".repeat(64), max_acu_limit: 2, can_submit: true, existing: null};
  assert.equal(helpers.investigationPreview(preview, "run-1").prompt, preview.prompt);
  assert.equal(helpers.investigationPreview(preview, "run-2"), null);
  assert.equal(helpers.investigationPreview({...preview, digest_hash: "short"}, "run-1"), null);
  assert.equal(helpers.investigationPreview({...preview, max_acu_limit: 3}, "run-1"), null);
  assert.equal(helpers.investigationPreview({...preview, prompt: ""}, "run-1"), null);
  assert.equal(helpers.investigationPreview({...preview, existing: {status: "unknown"}}, "run-1").can_submit, false);
  assert.equal(helpers.devinSessionUrl("https://app.devin.ai/sessions/devin-abc_123"), "https://app.devin.ai/sessions/devin-abc_123");
  for (const url of ["https://evil.example/sessions/123", "https://app.devin.ai.evil.example/sessions/123", "javascript:alert(1)", "https://app.devin.ai/sessions/123?token=private"]) assert.equal(helpers.devinSessionUrl(url), null);
  assert.equal(helpers.engineeringStatus({status: "running"}), "Devin is investigating");
  assert.equal(helpers.engineeringStatus({status: "running", status_detail: "finished"}), "Investigation finished");
  assert.equal(helpers.engineeringStatus({status: "running", status_detail: "waiting_for_user"}), "Waiting for operator input");
  assert.equal(helpers.engineeringStatus({status: "running", updated_at: NOW - 61000}, NOW), "Last recorded: running");
  assert.equal(helpers.engineeringStatus({status: "running", updated_at: NOW - 1000}, NOW), "Devin is investigating");
  assert.equal(helpers.engineeringStatus({status: "unknown"}), "Submission unconfirmed");
});

test("unavailable Devin submission never invents an existing investigation", () => {
  const ui = observerHarness({token: "test-operator-capability-with-at-least-32-characters"});
  ui.set({updated_at: NOW, state: {}, runs: [{id: "run-1"}]});
  ui.engineering("run-1");
  const data = {run_id: "run-1", prompt: "Prepared redacted brief", digest_hash: "b".repeat(64), max_acu_limit: 2, can_submit: false, existing: null};
  ui.handleEngineeringCommand({id: "unavailable", action: "prepare_investigation", status: "completed", data});
  ui.renderEngineering();
  assert.match(ui.element("devin-feedback").textContent, /submission is unavailable/);
  assert.doesNotMatch(ui.element("devin-feedback").textContent, /already has/);
  assert.equal(ui.element("devin-prompt").textContent, data.prompt);
  assert.equal(ui.element("devin-submit").disabled, true);
  ui.handleEngineeringCommand({id: "existing", action: "prepare_investigation", status: "completed", data: {...data, existing: {status: "unknown"}}});
  ui.renderEngineering();
  assert.match(ui.element("devin-feedback").textContent, /already has a recorded investigation/);
});

test("Devin submits only after private preparation and explicit confirmation of that brief", async () => {
  const token = "test-operator-capability-with-at-least-32-characters";
  const requests = [];
  let serial = 0;
  const preview = {run_id: "run-1", prompt: "Exact redacted brief\nNo screenshots included.", digest_hash: "b".repeat(64), max_acu_limit: 2, can_submit: true, existing: null};
  const ui = observerHarness({token, randomUUID: () => `command-${++serial}`, fetch: async (url, options) => {
    const body = JSON.parse(options.body);
    requests.push({url, options, body});
    return {ok: true, json: async () => ({command: {...body, status: "completed", data: body.action === "prepare_investigation" ? preview : {run_id: "run-1", status: "submitting", max_acu_limit: 2}}})};
  }});
  ui.set({updated_at: NOW, feed_status: "controller-offline", state: {}, runs: [{id: "run-1"}]});
  ui.engineering("run-1"); ui.renderEngineering();
  assert.equal(ui.element("devin-prepare").disabled, false, "An online publisher can investigate archived logs while gameplay is paused or offline");
  await ui.submitCommand("investigate", {run_id: "run-1", digest_hash: preview.digest_hash});
  assert.equal(requests.length, 0);
  await ui.submitCommand("prepare_investigation", {run_id: "run-1"});
  assert.equal(requests.length, 1);
  assert.equal(ui.element("devin-prompt").textContent, preview.prompt);
  assert.equal(ui.element("devin-review").hidden, false);
  assert.equal(ui.element("devin-submit").disabled, true);
  await ui.submitCommand("investigate", {run_id: "run-1", digest_hash: preview.digest_hash});
  assert.equal(requests.length, 1);
  ui.element("devin-confirm").checked = true; ui.renderEngineering();
  assert.equal(ui.element("devin-submit").disabled, false);
  await ui.submitCommand("investigate", {run_id: "run-1", digest_hash: "c".repeat(64)});
  assert.equal(requests.length, 1, "An unreviewed digest cannot be submitted");
  await ui.submitCommand("investigate", {run_id: "run-1", digest_hash: preview.digest_hash});
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[1].body, {id: "command-2", action: "investigate", run_id: "run-1", digest_hash: preview.digest_hash});
  assert.equal(requests[1].options.headers.Authorization, `Bearer ${token}`);
  assert.ok(!requests[1].options.body.includes(preview.prompt));
  assert.equal(ui.element("devin-prompt").textContent, "");
  assert.equal(ui.element("devin-submit").disabled, true);
  assert.equal(ui.element("devin-session-status").textContent, "Submitting to Devin");
});

test("public findings remain viewable but public viewers cannot prepare or submit investigations", async () => {
  const ui = observerHarness();
  ui.set({updated_at: NOW, state: {}, runs: [{id: "run-1"}]});
  ui.engineering("run-1", {status: "running", status_detail: "finished", url: "https://app.devin.ai/sessions/session-1", structured_output: {summary: "<script>Never execute report text</script>", likely_causes: ["Route was blocked"], proposed_tests: ["Test blocked route recovery"], test_patches: [{relative_path: "tests/test_route.py", purpose: "Exercise recovery", code: "<script>plain code only</script>", validation_status: "not_run"}]}});
  ui.renderEngineering();
  assert.equal(ui.element("devin-prepare").disabled, true);
  assert.equal(ui.element("devin-submit").disabled, true);
  assert.equal(ui.element("devin-findings-summary").textContent, "<script>Never execute report text</script>");
  assert.equal(ui.element("devin-causes").textContent, "Route was blocked");
  assert.equal(ui.element("devin-session-link").hidden, false);
  const patch = ui.element("devin-patches").nodes[0];
  assert.equal(patch.appended[2].textContent, "<script>plain code only</script>");
  ui.renderEngineering();
  assert.equal(ui.element("devin-patches").nodes[0], patch, "Polling must not collapse an open test proposal");
  await ui.submitCommand("prepare_investigation", {run_id: "run-1"});
  assert.equal(ui.element("devin-review").hidden, true);
});

test("locking controls during preparation ignores the late private brief", async () => {
  let finish;
  const ui = observerHarness({token: "test-operator-capability-with-at-least-32-characters", fetch: () => new Promise((resolve) => {finish = resolve;})});
  ui.set({updated_at: NOW, state: {}, runs: [{id: "run-1"}]});
  ui.engineering("run-1");
  const pending = ui.submitCommand("prepare_investigation", {run_id: "run-1"});
  ui.lockControls();
  finish({ok: true, json: async () => ({command: {id: "ad67598a-6d1f-4e66-8730-0e2e76c1cd28", action: "prepare_investigation", status: "completed", data: {run_id: "run-1", prompt: "PRIVATE PREVIEW", digest_hash: "a".repeat(64), max_acu_limit: 2, can_submit: true, existing: null}}})});
  await pending;
  assert.equal(ui.element("devin-review").hidden, true);
  assert.equal(ui.element("devin-prompt").textContent, "");
  assert.equal(ui.element("devin-submit").disabled, true);
  assert.equal(ui.stored.has("holospeed.operator"), false);
});

test("local execution requires fresh matching skill evidence and does not invent portal item progress", () => {
  const ui = observerHarness();
  const action = {skill: "mine", item: "minecraft:stone", target_count: 3};
  const event = {seq: 2, timestamp: new Date(NOW).toISOString(), type: "skill_progress", data: {inventory_count: 1, skill: {kind: "mine", item: "minecraft:stone", target_count: 3, active: true, phase: "mining"}}};
  const state = {run_id: "live", status: "running", action, events: [{seq: 1, type: "skill_started"}, event]};
  ui.resources(state); ui.renderMission(state, {fresh: true});
  assert.equal(ui.element("mission-executor").classList.contains("active"), true);
  assert.equal(ui.element("executor-progress").value, 1);
  assert.equal(ui.element("executor-progress").max, 3);
  ui.setNow(NOW + 6000); ui.renderMission(state, {fresh: true});
  assert.equal(ui.element("mission-executor").classList.contains("active"), false);
  assert.equal(ui.element("executor-progress").hidden, true);
  ui.setNow(NOW);
  const portal = {run_id: "portal", status: "running", action: {skill: "portal", item: "minecraft:nether_portal", target_count: 1}, events: [{...event, data: {inventory_count: 0, skill: {kind: "portal", item: "minecraft:nether_portal", target_count: 1, active: true, phase: "repairing_frame"}}}]};
  ui.resources(portal); ui.renderMission(portal, {fresh: true});
  assert.equal(ui.element("mission-executor").classList.contains("active"), true);
  assert.equal(ui.element("skill-count").textContent, "Portal route");
  assert.equal(ui.element("skill-name").textContent, "Navigate · build · enter Nether");
});

test("from-scratch progress requires its own explicit claims, without imposing an axe or rewriting historical runs", () => {
  const legacy = helpers.routeProgress({run_id: "historical", milestones: [{id: "stone_tools"}, {id: "ignition_acquired"}]});
  assert.equal(legacy.key, "ruined_portal");
  assert.equal(legacy.completed, 2);
  assert.equal(legacy.ids.length, 11);
  assert.ok(legacy.ids.includes("chest_looted"));
  const scratch = helpers.routeProgress({run_id: "new", route: "from_scratch", inventory: {"minecraft:water_bucket": 1}, milestones: [{id: "wood"}, {id: "wood"}, {id: "stone_tools"}, {id: "ignition_acquired"}]});
  assert.equal(scratch.completed, 1, "Duplicates and old composite/ignition claims cannot satisfy new checkpoints");
  assert.equal(scratch.ids.length, 14);
  assert.ok(scratch.ids.includes("stone_pickaxe"));
  assert.ok(scratch.ids.includes("flint_and_steel"));
  assert.ok(!scratch.ids.some((id) => id.includes("axe") && !id.includes("pickaxe")));
  assert.ok(!scratch.ids.includes("chest_looted"));
  assert.equal(helpers.routeProgress().key, "from_scratch");
  assert.equal(helpers.routeProgress({route: "ruined_portal"}).ids.length, 11);
});

test("the visual route switches by current-run metadata and marks only matching recorded evidence", () => {
  const ui = observerHarness();
  const scratch = {run_id: "new", route: "from_scratch", milestones: [{id: "stone_pickaxe", elapsed_ms: 23000, verification: "game_state", evidence: {inventory: {"minecraft:stone_pickaxe": 1}}}]};
  ui.renderMilestones(scratch);
  assert.equal(ui.element("route-panel").dataset.route, "from_scratch");
  assert.equal(ui.element("milestone-count").textContent, "1 / 14 milestones");
  assert.equal(ui.element("preparation-milestones").nodes.length, 5);
  assert.equal(ui.element("milestones").nodes.length, 9);
  const pick = ui.element("preparation-milestones").nodes.find((node) => node.dataset.milestone === "stone_pickaxe");
  assert.equal(pick.classList.contains("claimed"), true);
  assert.match(pick.querySelector("div > span").textContent, /00:23.*Confirmed from game state/);
  const previous = ui.element("milestones").nodes;
  ui.renderMilestones(scratch);
  assert.equal(ui.element("milestones").nodes, previous, "Routine polling preserves the current route's DOM");
  ui.renderMilestones({run_id: "old", milestones: [{id: "portal_repaired", verification: "game_state"}]});
  assert.equal(ui.element("progression-title").textContent, "Ruined portal route");
  assert.equal(ui.element("milestone-count").textContent, "1 / 11 milestones");
  assert.equal(ui.element("milestones").nodes.find((node) => node.dataset.milestone === "portal_repaired").classList.contains("claimed"), true);
  assert.ok(!ui.element("milestones").nodes.some((node) => node.dataset.milestone === "portal_built"));
});

test("password unlock clears the input and grants only this tab's existing operator capability", async () => {
  const token = "fictional-password-login-token-with-32-characters";
  const password = "test-only-password";
  const requests = [];
  const ui = observerHarness({fetch: async (url, options) => {
    requests.push({url, options});
    return {ok: true, json: async () => ({token})};
  }});
  ui.set({updated_at: NOW, feed_status: "online", state: {status: "paused", connected: true, run_id: "run-1"}});
  ui.renderControls();
  assert.equal(ui.element("control-resume").disabled, true);
  ui.element("operator-password").value = password;
  await ui.unlockControls();
  assert.equal(ui.element("operator-password").value, "");
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/operator/login");
  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.cache, "no-store");
  assert.equal(requests[0].options.redirect, "error");
  assert.deepEqual(JSON.parse(requests[0].options.body), {password});
  assert.equal(ui.stored.get("holospeed.operator"), token);
  assert.ok(![...ui.stored.values()].includes(password));
  assert.equal(ui.element("operator-login-form").hidden, true);
  assert.equal(ui.element("operator-footer-lock").hidden, false);
  assert.equal(ui.element("control-resume").disabled, false);
  assert.ok(!ui.element("operator-login-feedback").textContent.includes(token));
  ui.lockControls();
  assert.equal(ui.stored.has("holospeed.operator"), false);
  assert.equal(ui.element("operator-login-form").hidden, false);
  assert.equal(ui.element("operator-footer-lock").hidden, true);
  assert.equal(ui.element("control-resume").disabled, true);
});

test("failed password attempts and malformed tokens cannot unlock controls or expose server messages", async () => {
  for (const result of [{ok: false, status: 401}, {ok: false, status: 429}, {ok: false, status: 503}, {ok: true, json: async () => ({token: "invalid"})}]) {
    const ui = observerHarness({fetch: async () => result});
    ui.set({updated_at: NOW, feed_status: "online", state: {status: "paused", connected: true, run_id: "run-1"}});
    ui.element("operator-password").value = "test-only-password";
    await ui.unlockControls();
    assert.equal(ui.element("operator-password").value, "");
    assert.equal(ui.element("operator-status").textContent, "View only");
    assert.equal(ui.element("control-resume").disabled, true);
    assert.equal(ui.stored.has("holospeed.operator"), false);
    assert.equal(ui.element("operator-login-submit").disabled, false);
  }
});

test("a locked or superseded password request cannot restore operator access", async () => {
  let finish;
  let calls = 0;
  const ui = observerHarness({fetch: () => {calls++; return new Promise((resolve) => {finish = resolve;});}});
  ui.element("operator-password").value = "test-only-password";
  const pending = ui.unlockControls();
  assert.equal(ui.element("operator-password").value, "");
  assert.equal(ui.element("operator-login-submit").disabled, true);
  await ui.unlockControls();
  assert.equal(calls, 1);
  ui.lockControls();
  finish({ok: true, json: async () => ({token: "fictional-password-login-token-with-32-characters"})});
  await pending;
  assert.equal(ui.stored.has("holospeed.operator"), false);
  assert.equal(ui.element("operator-login-form").hidden, false);
  assert.equal(ui.element("operator-status").textContent, "View only");
});

test("typed voice instructions use the current run and immutable command protocol", async () => {
  const requests = [];
  const ui = observerHarness({token: "test-operator-capability-with-at-least-32-characters", fetch: async (url, options) => {
    const body = JSON.parse(options.body); requests.push({url, options, body});
    return {ok: true, json: async () => ({command: {...body, status: "completed", data: {intent: "pause", reply: "Gameplay paused."}}})};
  }});
  ui.set({updated_at: NOW, feed_status: "online", state: {run_id: "current-run", status: "running", events: []}});
  ui.journal({events: []}, "archived-run");
  ui.element("voice-text").value = " pause "; ui.sendTypedVoice();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/commands");
  assert.deepEqual(requests[0].body, {id: "ad67598a-6d1f-4e66-8730-0e2e76c1cd28", action: "voice", text: "pause", run_id: "current-run", session_id: "typed", utterance_id: "ad67598a-6d1f-4e66-8730-0e2e76c1cd28"});
  assert.equal(ui.element("voice-text").value, "");
  assert.equal(ui.element("voice-transcript").textContent, "pause");
  assert.equal(ui.element("voice-reply").textContent, "Gameplay paused.");
});

test("public viewers cannot start a microphone or submit typed instructions", () => {
  let starts = 0;
  const ui = observerHarness({HoloVoice: {Session: class {start() {starts++;}}}});
  ui.set({updated_at: NOW, feed_status: "online", state: {run_id: "run", status: "running"}});
  ui.renderVoice(); ui.toggleVoice();
  ui.element("voice-text").value = "pause"; ui.sendTypedVoice();
  assert.equal(starts, 0);
  assert.equal(ui.element("voice-toggle").disabled, true);
  assert.equal(ui.element("voice-send").disabled, true);
});

test("queued speech cannot spill into another run", async () => {
  const ui = observerHarness({token: "test-operator-capability-with-at-least-32-characters"});
  ui.set({updated_at: NOW, feed_status: "online", state: {run_id: "first", status: "running"}});
  ui.command({id: "existing", action: "pause", status: "pending"});
  ui.queueVoice({text: "collect more wood", run_id: "first", session_id: "typed", utterance_id: "utterance-1"});
  ui.set({updated_at: NOW, feed_status: "online", state: {run_id: "second", status: "running"}});
  ui.command(null); await ui.sendNextVoice();
  assert.match(ui.element("voice-feedback").textContent, /previous run/);
});

test("uncertain voice delivery only checks the original command ID", async () => {
  const requests = [];
  const ui = observerHarness({token: "test-operator-capability-with-at-least-32-characters", fetch: async (url, options) => {
    requests.push({url, method: options.method});
    if (options.method === "POST") throw new Error("Network interrupted after send");
    return {ok: true, json: async () => ({command: {id: "ad67598a-6d1f-4e66-8730-0e2e76c1cd28", action: "voice", status: "completed", data: {intent: "pause", reply: "Paused."}}})};
  }});
  ui.set({updated_at: NOW, feed_status: "online", state: {run_id: "run", status: "running"}});
  ui.queueVoice({text: "pause", run_id: "run", session_id: "typed", utterance_id: "utterance-1"});
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(requests.map((request) => request.method), ["POST", "GET"]);
  assert.equal(requests[1].url, "/api/commands/ad67598a-6d1f-4e66-8730-0e2e76c1cd28");
  assert.equal(ui.element("voice-reply").textContent, "Paused.");
});

test("explicit queue-busy rejection may retry the same unaccepted voice identity after waiting", async () => {
  const bodies = [];
  const ui = observerHarness({token: "test-operator-capability-with-at-least-32-characters", fetch: async (_url, options) => {
    const body = JSON.parse(options.body); bodies.push(body);
    if (bodies.length === 1) return {ok: false, status: 409, json: async () => ({error: "Another command is pending"})};
    return {ok: true, json: async () => ({command: {...body, status: "completed", data: {intent: "pause", reply: "Paused."}}})};
  }});
  ui.set({updated_at: NOW, feed_status: "online", state: {run_id: "run", status: "running"}});
  ui.queueVoice({text: "pause", run_id: "run", session_id: "typed", utterance_id: "utterance-1"});
  await new Promise((resolve) => setImmediate(resolve));
  ui.renderVoice(); assert.equal(bodies.length, 1);
  ui.setNow(NOW + 1000); await ui.sendNextVoice();
  assert.equal(bodies.length, 2); assert.deepEqual(bodies[1], bodies[0]);
});

test("locking controls stops the microphone before callbacks can dispatch queued speech", async () => {
  let recording;
  const ui = observerHarness({token: "test-operator-capability-with-at-least-32-characters", HoloVoice: {Session: class {
    constructor(options) {this.options = options; recording = this;}
    start() {this.active = true; this.options.onState("listening");}
    cancel() {this.active = false; this.cancelled = true; this.options.onState("idle");}
  }}});
  ui.set({updated_at: NOW, feed_status: "online", state: {run_id: "run", status: "running"}});
  ui.toggleVoice();
  ui.command({id: "existing", action: "pause", status: "pending"});
  ui.queueVoice({text: "collect wood", run_id: "run", session_id: "typed", utterance_id: "utterance-1"});
  ui.command(null); ui.lockControls();
  assert.equal(recording.cancelled, true);
  recording.options.onFinal({text: "pause", session_id: "test", utterance_id: "late"});
  await ui.sendNextVoice();
  assert.equal(ui.element("voice-toggle").disabled, true);
  assert.equal(ui.element("operator-status").textContent, "View only");
});

test("public voice captions pair the latest instruction with its own asynchronous reply", () => {
  const ui = observerHarness();
  ui.set({updated_at: NOW, state: {run_id: "run", events: [
    {seq: 1, type: "voice_received", data: {command_id: "old", text: "old question"}},
    {seq: 2, type: "voice_received", data: {command_id: "new", text: "what are you doing?"}},
    {seq: 3, type: "voice_reply", data: {command_id: "new", reply: "I am gathering iron."}},
    {seq: 4, type: "voice_reply", data: {command_id: "old", reply: "Late reply to the old question."}}
  ]}});
  ui.renderVoice();
  assert.equal(ui.element("voice-transcript").textContent, "what are you doing?");
  assert.equal(ui.element("voice-reply").textContent, "I am gathering iron.");
  assert.equal(helpers.eventDescription({type: "voice_received", data: {source: "gladia", text: "pause"}}), "Gladia transcript · pause");
});

test("Holo voice answers cannot replace or animate the gameplay strategist's decision", () => {
  const gameplay = inference(1, "holo", "complete", {decision_role: "skill_selection", rationale: "Mine the exposed iron."});
  const question = inference(2, "holo", "thinking", {decision_role: "voice_response", input_source: "recorded_state", task: "Answer the operator"});
  const answer = inference(3, "holo", "complete", {decision_role: "voice_response", input_source: "recorded_state", rationale: "A chat reply"});
  const stage = helpers.inferenceStage([gameplay, question, answer], "holo", {status: "running"}, true, NOW);
  assert.equal(stage.active, false);
  assert.equal(stage.rationale, "Mine the exposed iron.");
  assert.equal(stage.latest.seq, 1);
  assert.equal(helpers.inferenceStage([question], "holo", {status: "running"}, true, NOW).status, "Standby");
});

test("launch preparation exposes cancellation without a current run or game connection", () => {
  for (const status of ["idle", "paused", "unavailable"]) {
    const state = {status, connected: false, run_id: null, launch: {status: "preparing", stage: "loading"}};
    const controls = helpers.controlAvailability(state, true, true, false);
    assert.equal(controls.pause, true);
    assert.equal(controls.stop, true);
    assert.equal(controls.start_new, false);
    assert.equal(controls.resume, false);
    assert.equal(Object.values(helpers.controlAvailability(state, false, true, false)).some(Boolean), false);
    assert.equal(Object.values(helpers.controlAvailability(state, true, false, false)).some(Boolean), false);
  }
  const menu = {status: "idle", connected: true, run_id: null, launch: {status: "cancelled", stage: "cancelled"}};
  assert.equal(helpers.controlAvailability(menu, true, true, false).start_new, true, "A connected title screen can launch a fresh world");
});

test("launch stages do not claim completion from a command acceptance or invent percentages", () => {
  assert.equal(helpers.launchProgress(null), null);
  assert.equal(helpers.launchProgress({status: "accepted"}), null);
  for (const [stage, label] of Object.entries({saving: "Saving the previous world", copying: "Copying a clean world", loading: "Loading Minecraft", verifying: "Verifying the fresh spawn", starting: "Starting the agent"})) {
    const progress = helpers.launchProgress({status: "preparing", stage, run_id: null});
    assert.equal(progress.active, true); assert.equal(progress.label, label); assert.equal(progress.runId, null);
  }
  assert.equal(helpers.launchProgress({status: "running", stage: "running", run_id: "new"}).label, "Fresh run started");
  assert.equal(helpers.launchProgress({status: "failed", stage: "failed"}).active, false);
  assert.equal(helpers.launchProgress({status: "cancelled", stage: "cancelled"}).label, "Launch cancelled");
  assert.equal(helpers.launchProgress({status: "interrupted", stage: "interrupted"}).label, "Launch interrupted");
});

test("preparation cannot display the archived run's time, milestones, or decisions as a new run", () => {
  const ui = observerHarness({token: "test-operator-capability-with-at-least-32-characters"});
  ui.set({updated_at: NOW, feed_status: "online", runs: [], state: {status: "paused", connected: false, run_id: "previous", elapsed_ms: 123000, objective: "Previous run objective", metrics: {actions: 99}, milestones: [{id: "nether", verification: "game_state"}], events: [inference(1, "holo", "complete", {rationale: "Previous plan"})], launch: {status: "preparing", stage: "copying", detail: "Making a new clean copy.", world_name: "fresh-copy", run_id: null}}});
  ui.renderState();
  assert.equal(ui.element("agent-status").textContent, "Preparing new run");
  assert.match(ui.element("run-context").textContent, /gameplay has not started/);
  assert.equal(ui.element("elapsed").textContent, "—");
  assert.equal(ui.element("actions").textContent, "—");
  assert.equal(ui.element("route-panel").hidden, true);
  assert.equal(ui.element("mission-holo-rationale").textContent, "No decision recorded yet.");
  assert.equal(ui.element("mission-flow-state").textContent, "Waiting for the fresh world");
  assert.equal(ui.element("control-pause").disabled, false);
  assert.equal(ui.element("control-stop").disabled, false);
  assert.equal(ui.element("control-start_new").disabled, true);
  assert.equal(ui.element("launch-heading").textContent, "Copying a clean world");
  assert.equal(ui.element("launch-run").hidden, true);
});

test("launch failures, stale stages, and missing metadata stay explicit and restore normal control wording", () => {
  const ui = observerHarness();
  ui.renderLaunch({launch: {status: "preparing", stage: "verifying", detail: "Checking empty inventory."}}, {fresh: false});
  assert.equal(ui.element("launch-state").textContent, "LAST RECORDED");
  assert.equal(ui.element("run-launch").classList.contains("active"), false);
  ui.renderLaunch({launch: {status: "failed", stage: "failed", detail: "The world could not be verified.", world_name: "preserved-failed-copy", run_id: null}}, {fresh: true});
  assert.equal(ui.element("launch-heading").textContent, "Launch failed");
  assert.equal(ui.element("launch-detail").textContent, "The world could not be verified.");
  assert.equal(ui.element("launch-stages").hidden, true);
  assert.equal(ui.element("launch-world").textContent, "preserved-failed-copy");
  assert.equal(ui.element("control-stop").title, "Stop and archive the current run");
  ui.renderLaunch({}, {fresh: true});
  assert.equal(ui.element("run-launch").hidden, true);
  assert.equal(ui.element("control-pause").title, "Pause gameplay");
  const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
  assert.ok(!html.includes("New recording · Current world"));
  assert.match(html, /Fresh world · Automatic start/);
});

test("New run stays disabled for old controllers until the clean-launch capability is published", () => {
  const ui = observerHarness({token: "test-operator-capability-with-at-least-32-characters"});
  const old = {status: "paused", connected: true, run_id: "previous"};
  assert.equal(helpers.controlAvailability(old, true, true, false).start_new, false);
  ui.set({updated_at: NOW, feed_status: "online", state: old}); ui.renderControls();
  assert.equal(ui.element("control-start_new").disabled, true);
  assert.equal(ui.element("operator-world-note").textContent, "Launcher not connected");
  assert.match(ui.element("control-feedback").textContent, /Launcher not connected/);
  assert.equal(ui.element("control-resume").disabled, false, "Existing controls remain compatible during rollout");
  ui.set({updated_at: NOW, feed_status: "online", state: {...old, launch: null}}); ui.renderControls();
  assert.equal(ui.element("control-start_new").disabled, false);
  assert.equal(ui.element("operator-world-note").textContent, "Fresh world · Automatic start");
});

test("a fresh-run request masks the previous attempt immediately and waits for its own launch ID", async () => {
  let finish;
  const requests = [];
  const id = "ad67598a-6d1f-4e66-8730-0e2e76c1cd28";
  const ui = observerHarness({token: "test-operator-capability-with-at-least-32-characters", fetch: (_url, options) => {
    requests.push(JSON.parse(options.body));
    return new Promise((resolve) => {finish = resolve;});
  }});
  const previous = {status: "paused", connected: true, run_id: "previous", elapsed_ms: 123000, metrics: {actions: 99}, milestones: [{id: "nether", verification: "game_state"}], events: [inference(1, "holo", "complete", {rationale: "Previous plan"})], launch: {id: "previous-launch", status: "interrupted", stage: "interrupted", detail: "The previous attempt was interrupted.", world_name: "previous-world"}};
  ui.set({updated_at: NOW, feed_status: "online", runs: [], state: previous});
  ui.renderControls();
  const pending = ui.submitCommand("start_new");
  assert.equal(ui.element("launch-heading").textContent, "Requesting a fresh run");
  assert.equal(ui.element("launch-state").textContent, "REQUESTED");
  assert.equal(ui.element("elapsed").textContent, "—");
  assert.equal(ui.element("actions").textContent, "—");
  assert.equal(ui.element("route-panel").hidden, true);
  assert.equal(ui.element("mission-holo-rationale").textContent, "No decision recorded yet.");
  assert.equal(ui.element("launch-world").hidden, true);
  assert.equal(ui.element("launch-stages").hidden, true, "No actual preparation stage has been published");
  finish({ok: true, json: async () => ({command: {id, action: "start_new", status: "completed", detail: "Preparation accepted."}})});
  await pending;
  assert.equal(ui.element("launch-heading").textContent, "Fresh run accepted");
  assert.equal(ui.element("launch-state").textContent, "ACCEPTED");
  assert.equal(ui.element("agent-status").textContent, "Awaiting launch status");
  assert.equal(ui.element("control-start_new").disabled, true);
  assert.equal(ui.element("control-resume").disabled, true);
  assert.equal(ui.element("control-pause").disabled, false);
  assert.equal(ui.element("control-stop").disabled, false);
  await ui.submitCommand("start_new");
  assert.equal(requests.length, 1, "Acceptance must not reopen New run during publication lag");
  ui.set({updated_at: NOW, feed_status: "online", runs: [], state: {...previous, connected: false, launch: {...previous.launch, status: "failed", detail: "An old failure."}}});
  ui.renderState();
  assert.equal(ui.element("launch-heading").textContent, "Fresh run accepted");
  assert.equal(ui.element("control-pause").disabled, false, "An accepted launch remains cancellable while Minecraft reloads");
  assert.equal(ui.element("control-stop").disabled, false);
  ui.set({updated_at: NOW, feed_status: "online", runs: [], state: {...previous, launch: {id, status: "preparing", stage: "loading", world_name: "new-world", detail: "Loading the new copy."}}});
  ui.renderState();
  assert.equal(ui.element("launch-heading").textContent, "Loading Minecraft");
  assert.equal(ui.element("launch-stages").hidden, false);
  assert.equal(ui.element("launch-world").textContent, "new-world");
});

test("cancelling an accepted launch keeps its identity until the matching cancellation arrives", async () => {
  let serial = 0;
  const requests = [];
  const ui = observerHarness({token: "test-operator-capability-with-at-least-32-characters", randomUUID: () => `attempt-${++serial}`, fetch: async (_url, options) => {
    const command = JSON.parse(options.body); requests.push(command);
    return {ok: true, json: async () => ({command: {...command, status: "completed", detail: "Accepted."}})};
  }});
  const previous = {status: "idle", connected: true, run_id: null, launch: {id: "old", status: "failed", stage: "failed", detail: "Old failure."}};
  ui.set({updated_at: NOW, feed_status: "online", runs: [], state: previous});
  ui.renderControls(); await ui.submitCommand("start_new"); await ui.submitCommand("stop");
  ui.renderState();
  assert.deepEqual(requests.map((request) => request.action), ["start_new", "stop"]);
  assert.equal(ui.element("launch-heading").textContent, "Fresh run accepted");
  assert.equal(ui.element("control-start_new").disabled, true);
  ui.set({updated_at: NOW, feed_status: "online", runs: [], state: {...previous, launch: {id: "attempt-1", status: "cancelled", stage: "cancelled", detail: "This launch was cancelled."}}});
  ui.renderState();
  assert.equal(ui.element("launch-heading").textContent, "Launch cancelled");
  assert.equal(ui.element("launch-detail").textContent, "This launch was cancelled.");
  assert.equal(ui.element("control-start_new").disabled, false);
});

test("only the requested launch can supply an immediate failed or running outcome", async () => {
  for (const status of ["failed", "running"]) {
    const id = "ad67598a-6d1f-4e66-8730-0e2e76c1cd28";
    const ui = observerHarness({token: "test-operator-capability-with-at-least-32-characters", fetch: async () => ({ok: true, json: async () => ({command: {id, action: "start_new", status: "completed"}})})});
    const state = {status: "idle", connected: true, run_id: null, launch: {id: "old", status, stage: status}};
    ui.set({updated_at: NOW, feed_status: "online", runs: [], state}); ui.renderControls();
    await ui.submitCommand("start_new");
    assert.equal(ui.element("launch-heading").textContent, "Fresh run accepted");
    ui.set({updated_at: NOW, feed_status: "online", runs: [], state: {...state, launch: {id, status, stage: status, detail: "The matching outcome."}}}); ui.renderState();
    assert.equal(ui.element("launch-heading").textContent, status === "running" ? "Fresh run started" : "Launch failed");
    assert.equal(ui.element("launch-detail").textContent, "The matching outcome.");
    assert.equal(ui.element("route-panel").hidden, false);
  }
});

test("a pending New run still polls a real 404 without retrying its POST and releases after the existing deadline", async () => {
  const requests = [];
  const id = "ad67598a-6d1f-4e66-8730-0e2e76c1cd28";
  const ui = observerHarness({token: "test-operator-capability-with-at-least-32-characters", fetch: async (_url, options) => {
    requests.push(options.method);
    return options.method === "POST" ? {ok: true, json: async () => ({command: {id, action: "start_new", status: "pending"}})} : {ok: false, status: 404, json: async () => ({error: "Command not found."})};
  }});
  const state = {status: "idle", connected: true, run_id: null, launch: {id: "old", status: "interrupted", stage: "interrupted"}};
  ui.set({updated_at: NOW, feed_status: "online", runs: [], state}); ui.renderControls();
  await ui.submitCommand("start_new");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(requests, ["POST", "GET"]);
  assert.equal(ui.element("launch-heading").textContent, "Requesting a fresh run");
  assert.equal(ui.element("control-start_new").disabled, true);
  ui.setNow(NOW + 45001); ui.set({updated_at: NOW + 45001, feed_status: "online", runs: [], state});
  await ui.refreshCommand();
  assert.deepEqual(requests, ["POST", "GET", "GET"]);
  assert.equal(ui.element("launch-heading").textContent, "Launch interrupted");
  assert.equal(ui.element("control-start_new").disabled, false);
  assert.match(ui.element("control-feedback").textContent, /No command acceptance was confirmed/);
});

test("definitive New run rejection or expiration clears the pending launch presentation", async () => {
  for (const outcome of ["failed", "expired", "rejected"]) {
    const id = "ad67598a-6d1f-4e66-8730-0e2e76c1cd28";
    const ui = observerHarness({token: "test-operator-capability-with-at-least-32-characters", fetch: async () => outcome === "rejected" ? {ok: false, status: 409, json: async () => ({error: "Another command is pending."})} : {ok: true, json: async () => ({command: {id, action: "start_new", status: outcome}})}});
    ui.set({updated_at: NOW, feed_status: "online", runs: [], state: {status: "idle", connected: true, run_id: null, launch: null}});
    ui.renderControls(); await ui.submitCommand("start_new");
    assert.equal(ui.element("run-launch").hidden, true);
    assert.equal(ui.element("control-start_new").disabled, false);
    assert.equal(ui.element("route-panel").hidden, false);
  }
});

test("locking during a pending New run cannot strand an unconfirmed launch after unlock", async () => {
  let finish;
  const token = "test-operator-capability-with-at-least-32-characters";
  const ui = observerHarness({token, fetch: (url) => {
    if (url === "/api/operator/login") return Promise.resolve({ok: true, json: async () => ({token})});
    return new Promise((resolve) => {finish = resolve;});
  }});
  ui.set({updated_at: NOW, feed_status: "online", runs: [], state: {status: "idle", connected: true, run_id: null, launch: null}});
  ui.renderControls();
  const pending = ui.submitCommand("start_new");
  assert.equal(ui.element("launch-heading").textContent, "Requesting a fresh run");
  ui.lockControls("Controls locked by the operator.");
  assert.equal(ui.element("run-launch").hidden, true, "Lock discards an expectation that can no longer be reconciled by command polling");
  assert.equal(ui.element("route-panel").hidden, false);
  finish({ok: false, status: 409, json: async () => ({error: "The request was not accepted."})});
  await pending;
  assert.equal(ui.element("control-feedback").textContent, "Controls locked by the operator.");
  assert.equal(ui.stored.has("holospeed.command"), false);
  ui.element("operator-password").value = "test-password";
  await ui.unlockControls();
  assert.equal(ui.element("operator-status").textContent, "Operator access");
  assert.equal(ui.element("control-start_new").disabled, false);
  assert.equal(ui.element("run-launch").hidden, true);
});
