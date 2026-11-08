"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const context = {module: {exports: {}}, URL, ArrayBuffer};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/voice.js"), "utf8"), context);
const {Session, sessionGrant} = context.module.exports;
const ID = "2b2a4c55-6a67-4d10-a26e-3b387c3cc7dd";
const GRANT = {id: ID, url: "wss://api.gladia.io/audio/session?temporary=test-only", sample_rate: 16000};
function harness(options = {}) {
  const sockets = [], states = [], finals = [], partials = [], errors = [], contexts = [], timers = new Map();
  let serial = 0, grants = 0, permissions = 0;
  const track = {stopped: false, stop() {this.stopped = true;}};
  const stream = {getTracks: () => [track]};
  const node = () => ({connect() {}, disconnect() {this.disconnected = true;}});
  class Audio {
    constructor() {contexts.push(this); this.destination = {}; this.audioWorklet = {addModule: async (url) => {this.module = url;}};}
    resume() {return Promise.resolve();}
    close() {this.closed = true; return Promise.resolve();}
    createMediaStreamSource() {return node();}
    createGain() {return {...node(), gain: {value: 1}};}
  }
  class Socket {
    constructor(url) {this.url = url; this.readyState = 1; this.bufferedAmount = 0; this.sent = []; sockets.push(this);}
    send(value) {this.sent.push(value);}
    close() {this.closed = true; this.readyState = 3;}
    message(value) {this.onmessage?.({data: JSON.stringify(value)});}
  }
  const env = {
    navigator: {mediaDevices: {getUserMedia: async () => {permissions++; if (options.permission) return options.permission(); return stream;}}},
    AudioContext: Audio, AudioWorkletNode: class {constructor() {Object.assign(this, node(), {port: {}});}}, WebSocket: Socket, AbortController,
    setTimeout(fn, delay) {const id = ++serial; timers.set(id, {fn, delay}); return id;}, clearTimeout(id) {timers.delete(id);},
  };
  const session = new Session({env, requestSession: async () => {grants++; return options.grant || GRANT;}, onState: (value) => states.push(value), onFinal: (value) => finals.push(value), onPartial: (value) => partials.push(value), onError: (value) => errors.push(value)});
  return {session, env, sockets, states, finals, partials, errors, track, contexts, timers, get grants() {return grants;}, get permissions() {return permissions;}, tick(delay) {for (const [id, timer] of [...timers]) if (timer.delay === delay) {timers.delete(id); timer.fn();}}};
}
function allow(socket) {socket.message({type: "start_session", data: {audio_upload_allowed: true}});}
function transcript(socket, is_final, text, id = "utterance-1") {socket.message({type: "transcript", data: {id, is_final, utterance: {text}}});}

test("session grants allow only ephemeral Gladia WSS endpoints and the negotiated PCM format", () => {
  assert.equal(sessionGrant(GRANT).id, ID);
  for (const patch of [{url: "https://api.gladia.io/audio"}, {url: "wss://gladia.io.evil.example/audio"}, {url: "wss://user:password@api.gladia.io/audio"}, {url: "wss://api.gladia.io:444/audio"}, {url: "wss://evilgladia.io/audio"}, {sample_rate: 48000}, {id: "not-a-session"}]) assert.equal(sessionGrant({...GRANT, ...patch}), null);
});

test("nothing records until explicit start, and PCM waits for Gladia upload permission", async () => {
  const h = harness();
  assert.equal(h.permissions, 0); assert.equal(h.grants, 0);
  await h.session.start();
  assert.equal(h.permissions, 1); assert.equal(h.grants, 1);
  assert.equal(h.contexts[0].module, "/assets/voice-worklet.js");
  h.session.sendAudio(new ArrayBuffer(3200));
  assert.equal(h.sockets[0].sent.length, 0);
  allow(h.sockets[0]);
  assert.equal(h.session.state, "listening");
  h.session.sendAudio(new ArrayBuffer(3200));
  assert.equal(h.sockets[0].sent[0].byteLength, 3200);
  await h.session.start(); assert.equal(h.grants, 1, "A second click cannot create overlapping sessions");
});

test("partial words never become commands and duplicate final utterances are emitted once", async () => {
  const h = harness(); await h.session.start(); const socket = h.sockets[0];
  transcript(socket, true, "pause", "before-upload"); assert.equal(h.finals.length, 0);
  allow(socket);
  transcript(socket, false, "pause"); assert.equal(h.finals.length, 0); assert.equal(h.partials.at(-1), "pause");
  transcript(socket, true, " pause "); transcript(socket, true, "pause");
  assert.equal(h.finals.length, 1);
  assert.equal(h.finals[0].text, "pause"); assert.equal(h.finals[0].session_id, ID);
  transcript(socket, true, "x".repeat(1001), "too-long");
  assert.equal(h.finals.length, 1); assert.match(h.errors.at(-1), /too long/);
});

test("stopping releases every audio resource immediately and drains only the final transcript", async () => {
  const h = harness(); await h.session.start(); const socket = h.sockets[0]; allow(socket);
  h.session.stop();
  assert.equal(h.track.stopped, true); assert.equal(h.contexts[0].closed, true);
  assert.equal(h.session.state, "finishing");
  assert.deepEqual(JSON.parse(socket.sent.at(-1)), {type: "stop_recording"});
  h.session.sendAudio(new ArrayBuffer(3200)); assert.equal(socket.sent.length, 1);
  transcript(socket, true, "pause"); assert.equal(h.finals.length, 1);
  h.tick(3000); assert.equal(socket.closed, true); assert.equal(h.session.active, false);
});

test("recordings stop at two minutes, even if upload-ready messages repeat", async () => {
  const h = harness(); await h.session.start(); allow(h.sockets[0]);
  const timerIds = [...h.timers.keys()]; allow(h.sockets[0]);
  assert.deepEqual([...h.timers.keys()], timerIds);
  h.tick(120000); assert.equal(h.track.stopped, true); assert.equal(h.session.state, "finishing");
});

test("backpressure stops the microphone without reconnecting or silently dropping a command", async () => {
  const h = harness(); await h.session.start(); allow(h.sockets[0]);
  h.sockets[0].bufferedAmount = 262144;
  h.session.sendAudio(new ArrayBuffer(3200));
  assert.equal(h.track.stopped, true); assert.equal(h.session.state, "error");
  assert.equal(h.sockets[0].sent.length, 0); assert.equal(h.grants, 1);
  assert.match(h.errors.at(-1), /falling behind/);
});

test("permission denial or cancellation never leaves a recording or creates a late session", async () => {
  const denied = harness({permission: () => {throw Object.assign(new Error("denied"), {name: "NotAllowedError"});}});
  await denied.session.start(); assert.equal(denied.grants, 0); assert.equal(denied.contexts[0].closed, true);
  let allowPermission;
  const h = harness({permission: () => new Promise((resolve) => {allowPermission = resolve;})});
  const pending = h.session.start(); h.session.cancel();
  allowPermission({getTracks: () => [h.track]}); await pending;
  assert.equal(h.track.stopped, true); assert.equal(h.grants, 0); assert.equal(h.sockets.length, 0);
});

test("a missing provider handshake times out and closes the microphone", async () => {
  const h = harness(); await h.session.start(); h.tick(20000);
  assert.equal(h.track.stopped, true); assert.equal(h.sockets[0].closed, true); assert.equal(h.session.active, false);
});

test("the worklet emits actual signed little-endian mono PCM at 16 kHz", () => {
  const worklet = fs.readFileSync(path.join(__dirname, "../web/voice-worklet.js"), "utf8");
  for (const sampleRate of [16000, 44100, 48000]) {
    const frames = []; let Processor;
    vm.runInNewContext(worklet, {sampleRate, AudioWorkletProcessor: class {constructor() {this.port = {postMessage: (frame) => frames.push(frame)};}}, registerProcessor(name, type) {assert.equal(name, "holospeed-pcm"); Processor = type;}, ArrayBuffer, DataView});
    const recorder = new Processor();
    const samples = sampleRate / 10;
    recorder.process([[new Float32Array(samples).fill(0.5)]]);
    assert.equal(frames.length, 1, `${sampleRate} Hz input produces exactly 100ms of audio`);
    assert.equal(frames[0].byteLength, 3200);
    assert.equal(new DataView(frames[0]).getInt16(0, true), 16384);
    recorder.process([[new Float32Array(samples).fill(-1)]]);
    assert.equal(new DataView(frames[1]).getInt16(0, true), -32768);
  }
});

test("Gladia's observed start_recording and documented start_session acknowledgments need no data object", async () => {
  for (const type of ["start_recording", "start_session"]) {
    const h = harness(); await h.session.start();
    h.sockets[0].message({type});
    assert.equal(h.session.state, "listening");
    h.session.sendAudio(new ArrayBuffer(3200)); assert.equal(h.sockets[0].sent.length, 1);
  }
  const denied = harness(); await denied.session.start();
  denied.sockets[0].message({type: "start_session", data: {audio_upload_allowed: false}});
  assert.equal(denied.session.state, "error"); assert.equal(denied.track.stopped, true);
  assert.equal(denied.sockets[0].sent.length, 0);
});
