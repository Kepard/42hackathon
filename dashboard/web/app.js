(function () {
  "use strict";

  const FRESH_FRAME_MS = 15000;
  const CAMERA_INTERVAL_MS = 1000; // JPEG fallback; HLS is the primary video feed.
  const JOURNAL_REFRESH_MS = 5000;
  const MILESTONES = {
    wood: "Gather wood", crafting_table: "Craft a table", wooden_pickaxe: "Make a pickaxe", stone_tools: "Reach stone tools",
    portal_located: "Locate the portal", chest_looted: "Loot the portal chest", obsidian_acquired: "Acquire obsidian",
    ignition_acquired: "Acquire ignition", portal_repaired: "Repair the portal", portal_lit: "Light the portal", nether: "Nether entry"
  };
  const n = (value) => typeof value === "number" && Number.isFinite(value) ? value : 0;
  const count = (value) => n(value).toLocaleString();
  function timestamp(value) {
    if (typeof value === "number" && Number.isFinite(value)) return value < 1e12 ? value * 1000 : value;
    if (typeof value !== "string" || !value) return null;
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  function elapsed(value, decimals = false) {
    const seconds = Math.max(0, n(value)) / 1000;
    const minutes = Math.floor(seconds / 60);
    const base = `${String(minutes).padStart(2, "0")}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
    return decimals ? `${base}.${Math.floor((seconds % 1) * 10)}` : base;
  }
  function ageLabel(age) {
    if (age == null) return "No capture received";
    if (age < 1000) return "Captured just now";
    if (age < 60000) return `Captured ${Math.floor(age / 1000)}s ago`;
    if (age < 3600000) return `Captured ${Math.floor(age / 60000)}m ago`;
    return `Captured ${Math.floor(age / 3600000)}h ago`;
  }
  function latency(value) {
    if (value == null || !Number.isFinite(Number(value))) return "—";
    return Number(value) >= 1000 ? `${(Number(value) / 1000).toFixed(2)} s` : `${Math.round(Number(value))} ms`;
  }
  function captureState(envelope, now = Date.now(), reachable = true) {
    const captured = timestamp(envelope?.captured_at);
    const age = captured == null ? null : Math.max(0, now - captured);
    const status = envelope?.feed_status;
    const fresh = reachable && captured != null && now - captured >= -2000 && age < FRESH_FRAME_MS && !["offline", "stale"].includes(status);
    const label = !reachable ? "Disconnected" : status === "controller-offline" ? "Controller offline" : fresh ? "Live camera" : captured == null ? "Waiting" : "Stale frame";
    return {age, fresh, label, className: fresh && status !== "controller-offline" ? "live" : captured != null || !reachable ? "stale" : ""};
  }
  function telemetryState(envelope, now = Date.now(), reachable = true) {
    const updated = timestamp(envelope?.updated_at);
    const age = updated == null ? null : Math.max(0, now - updated);
    const fresh = reachable && age != null && now - updated >= -2000 && age < FRESH_FRAME_MS && !["controller-offline", "offline"].includes(envelope?.feed_status);
    const label = !reachable ? "Observer disconnected" : envelope?.feed_status === "controller-offline" ? "Controller offline" : age == null ? "Waiting for agent" : age >= FRESH_FRAME_MS ? "Telemetry stale" : "Feed offline";
    return {age, fresh, label};
  }
  function displayedFrameState(capturedAt, now = Date.now(), reachable = true) {
    const age = capturedAt == null ? null : Math.max(0, now - capturedAt);
    return {age, fresh: reachable && age != null && now - capturedAt >= -2000 && age < FRESH_FRAME_MS};
  }
  function videoPlaybackState(metadata, playback, now = Date.now()) {
    const capture = timestamp(metadata?.captured_at);
    const played = timestamp(playback?.playing_at);
    const captureAge = capture == null ? null : now - capture;
    const playedAge = played == null ? null : now - played;
    const frameAge = playback?.last_frame_at == null ? null : now - playback.last_frame_at;
    const behind = playback?.behind_live_seconds;
    const current = playedAge != null ? playedAge >= -2000 && playedAge < FRESH_FRAME_MS : typeof behind === "number" && behind >= 0 && behind < FRESH_FRAME_MS / 1000;
    const fresh = playback?.reachable === true && metadata?.status === "live" && captureAge != null && captureAge >= -2000 && captureAge < FRESH_FRAME_MS && frameAge != null && frameAge >= 0 && frameAge < 1500 && playback?.paused === false && current;
    return {fresh, captureAge, playedAge, frameAge};
  }
  function eventId(event, index = 0) { return event.seq != null ? `seq-${event.seq}` : `${event.type}-${event.elapsed_ms}-${index}`; }
  function providerName(value) {
    const name = String(value || "Model");
    return name.toLowerCase().startsWith("holo") ? "Holo4" : name.toLowerCase().startsWith("jev") ? "Jev" : name;
  }
  function milestoneLabel(claim, mode) {
    if (claim?.verification === "game_state") return "Confirmed from game state";
    if (claim?.verification === "simulated" || mode === "demo") return "Simulated";
    return "Visual claim";
  }
  function itemName(item) {
    return String(item || "Unknown item").replace(/^minecraft:/, "").replaceAll("_", " ");
  }
  function evidenceText(evidence) {
    if (typeof evidence === "string") return evidence;
    if (evidence?.inventory && typeof evidence.inventory === "object") return Object.entries(evidence.inventory).map(([item, value]) => `${itemName(item)} × ${value}`).join(", ");
    return evidence == null ? "" : JSON.stringify(evidence);
  }
  function latestEvent(events, type) {
    return events.filter((event) => event.type === type).reduce((latest, event) => !latest || n(event.seq) > n(latest.seq) || (n(event.seq) === n(latest.seq) && n(event.elapsed_ms) > n(latest.elapsed_ms)) ? event : latest, null);
  }
  function skillTelemetry(action, events) {
    if (!action?.skill) return null;
    const progress = latestEvent(events, "skill_progress");
    const boundary = latestEvent(events.filter((event) => ["skill_started", "skill_decision"].includes(event.type)).map((event) => ({...event, type: "boundary"})), "boundary");
    const skill = progress?.data?.skill;
    const matches = skill?.item === action.item && skill?.kind === action.skill && skill?.target_count === action.target_count && (!boundary || n(progress.seq) > n(boundary.seq));
    const value = matches ? progress.data.inventory_count : null;
    return {name: itemName(action.skill), item: itemName(action.item), target: action.target_count,
      count: typeof value === "number" && Number.isFinite(value) ? value : null,
      phase: matches ? skill.phase || "" : "", event: matches ? progress : null};
  }
  function actionDescription(action) {
    if (!action || typeof action !== "object") return "Input requested";
    if (action.skill) return `${itemName(action.skill)} · ${itemName(action.item)}${action.target_count != null ? ` · target ${action.target_count}` : ""}`;
    const parts = Array.isArray(action.keys) ? [...action.keys] : [];
    if (action.yaw_delta) parts.push(`yaw ${action.yaw_delta}°`);
    if (action.pitch_delta) parts.push(`pitch ${action.pitch_delta}°`);
    if (action.cursor) parts.push(`${action.cursor.button || "mouse"} click`);
    if (action.hotbar) parts.push(`slot ${action.hotbar}`);
    if (action.scroll) parts.push(`scroll ${action.scroll}`);
    return `${parts.join(" + ") || "Wait / no held input"}${action.duration_ticks != null ? ` · ${action.duration_ticks} ticks` : ""}`;
  }
  function eventDescription(event) {
    const data = event.data || {};
    if (event.type === "state_summary") return `Game state · tick ${data.tick ?? "—"} · ${Object.keys(data.inventory || {}).length} inventory item types`;
    if (event.type === "skill_decision") return `${providerName(data.provider)} · ${actionDescription(data.candidate)}${data.rationale ? ` · ${data.rationale}` : ""}`;
    if (event.type === "skill_started") return actionDescription(data);
    if (event.type === "skill_progress") return `${itemName(data.skill?.item)} · ${data.inventory_count ?? "—"} / ${data.skill?.target_count ?? "—"}${data.skill?.phase ? ` · ${data.skill.phase}` : ""}`;
    if (["skill_completed", "skill_failed"].includes(event.type)) return `${itemName(data.item)} · ${data.reason || "Skill result recorded"}`;
    if (event.type === "decision" || event.type === "plan") return `${providerName(data.provider)} · ${data.decision?.objective || data.objective || "Visual plan recorded"}${event.type === "plan" ? " · awaiting validation" : ""}`;
    if (event.type === "inference_metrics") {
      const usage = data.usage || {};
      const input = usage.input_tokens ?? usage.prompt_tokens;
      const output = usage.output_tokens ?? usage.completion_tokens;
      return `${providerName(data.provider)} · ${latency(data.latency_ms)}${input != null || output != null ? ` · ${input == null ? "—" : count(input)} in / ${output == null ? "—" : count(output)} out` : " · usage not reported"}`;
    }
    if (event.type === "action") return actionDescription(data.action || data);
    if (["ack", "bridge_ack", "raw_bridge_ack"].includes(event.type)) return `Input ${data.status || "acknowledged"}${data.reason ? ` · ${data.reason}` : ""}`;
    if (event.type === "observation") return `Frame ${String(data.frame_id || "captured").slice(0, 12)}${data.width ? ` · ${data.width} × ${data.height}` : ""}`;
    if (event.type === "milestone") return `${milestoneLabel(data)}: ${MILESTONES[data.id || data.milestone] || data.id || data.milestone || "progress"}${data.evidence ? ` · ${evidenceText(data.evidence)}` : ""}`;
    if (event.type === "model_call") return `${providerName(data.provider)} request${data.error ? ` · ${data.error}` : ` · ${latency(data.latency_ms)}`}`;
    if (event.type === "bridge_command") return data.type === "action" ? actionDescription(data.action) : `Bridge ${data.type || "command"}${data.reason ? ` · ${data.reason}` : ""}`;
    return String(data.detail || data.reason || data.error || data.message || data.name || data.status || "Recorded event");
  }
  function eventTypeLabel(type) {
    return ({inference_metrics: "metrics", model_call: "model call", bridge_command: "bridge", bridge_ack: "bridge ACK", raw_bridge_ack: "raw ACK", run_started: "run started", run_finished: "run ended"})[type] || String(type || "event").replaceAll("_", " ");
  }
  function mergeEvents(previous, next) {
    const map = new Map();
    previous.forEach((event, index) => map.set(eventId(event, index), event));
    next.forEach((event, index) => map.set(eventId(event, index), event));
    return [...map.values()].sort((a, b) => n(a.elapsed_ms) - n(b.elapsed_ms) || n(a.seq) - n(b.seq));
  }
  function operatorCredential(location, history, storage) {
    const key = "holospeed.operator";
    const valid = (value) => typeof value === "string" && /^[A-Za-z0-9_-]{32,256}$/.test(value);
    let token = "";
    try { token = storage?.getItem(key) || ""; } catch { /* Storage may be blocked. */ }
    const fragment = new URLSearchParams((location?.hash || "").replace(/^#/, ""));
    if (fragment.has("control")) {
      token = fragment.get("control") || "";
      fragment.delete("control");
      // Remove the capability before any links, camera, or API requests run.
      history?.replaceState(null, "", `${location.pathname || "/"}${location.search || ""}${fragment.size ? `#${fragment}` : ""}`);
      try { if (valid(token)) storage?.setItem(key, token); else storage?.removeItem(key); } catch { /* Keep only this page's memory. */ }
    }
    return valid(token) ? token : "";
  }
  function controlAvailability(state, fresh, unlocked, pending) {
    const ready = Boolean(unlocked && fresh && !pending && state?.status !== "unavailable");
    return {
      start_new: ready && state?.connected === true,
      resume: ready && state?.connected === true && Boolean(state?.run_id) && ["paused", "error"].includes(state?.status),
      pause: ready && state?.status === "running",
      stop: ready && Boolean(state?.run_id),
    };
  }

  // Pure helpers can be tested in Node without a browser or network.
  if (typeof module === "object" && module.exports) {
    module.exports = {timestamp, elapsed, ageLabel, latency, captureState, telemetryState, displayedFrameState, videoPlaybackState, milestoneLabel, itemName, latestEvent, skillTelemetry, eventDescription, eventTypeLabel, mergeEvents, operatorCredential, controlAvailability};
    return;
  }

  const $ = (id) => document.getElementById(id);
  const text = (id, value) => { $(id).textContent = value; };
  let envelope = null;
  let reachable = false;
  let cameraEnvelope = null;
  let cameraReachable = null;
  let videoMetadata = null;
  let videoReachable = false;
  let videoGeneration = null;
  let videoRetryAt = 0;
  let videoHls = null;
  let videoLastFrameAt = null;
  let videoLastMediaTime = null;
  let videoFrameSamples = [];
  let videoFrameCallbackStarted = false;
  let videoSupported = null;
  let lastCaptureSeq = null;
  let pendingCaptureSeq = null;
  let displayedCapturedAt = null;
  let selectedRun = null;
  let followLive = true;
  let journal = {summary: null, events: []};
  let journalLoaded = false;
  let journalBusy = false;
  let journalRequestVersion = 0;
  let journalLastFetched = 0;
  let shownEvents = 150;
  let searchTimer;
  let notificationTimer;
  let lastRunListSignature = "";
  let lastLiveEventSignature = "";
  let evidenceRun = null;
  let resourceEvidence = [];
  const expandedEvents = new Set();
  let storage = null;
  try { storage = typeof sessionStorage !== "undefined" ? sessionStorage : null; } catch { /* Private browsing may deny storage. */ }
  let operatorToken = operatorCredential(typeof location !== "undefined" ? location : null, typeof history !== "undefined" ? history : null, storage);
  let operatorCommand = null;
  let commandBusy = false;
  let commandChecking = false;
  let commandLastChecked = 0;
  let commandMessage = "";
  try {
    const saved = storage?.getItem("holospeed.command");
    if (operatorToken && saved) {
      const value = JSON.parse(saved);
      if (/^[0-9a-f-]{36}$/i.test(value.id)) operatorCommand = {id: value.id, status: "pending", submitted_at: value.submitted_at};
    }
  } catch { /* An invalid local history must not trigger commands. */ }

  async function get(path, timeout = 10000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(path, {signal: controller.signal, cache: "no-store"});
      if (!response.ok) throw new Error(response.status === 404 ? "This run is not available yet." : `Feed request failed (${response.status}).`);
      return await response.json();
    } finally { clearTimeout(timer); }
  }
  function notify(message) {
    clearTimeout(notificationTimer);
    text("notification", message);
    $("notification").hidden = false;
    notificationTimer = setTimeout(() => { $("notification").hidden = true; }, 5000);
  }
  function setStatus(element, label, className = "neutral") {
    const dot = document.createElement("i");
    element.replaceChildren(dot, document.createTextNode(label));
    element.className = `status-pill ${className}`;
  }
  function updateCapture() {
    const camera = cameraEnvelope || envelope;
    const cameraOnline = cameraReachable == null ? reachable : cameraReachable;
    const status = captureState(camera, Date.now(), cameraOnline);
    const shown = displayedFrameState(displayedCapturedAt, Date.now(), cameraOnline);
    const displayLive = shown.fresh && status.fresh;
    const hasImage = !$("game-frame").hidden;
    const feedLabel = $("feed-status").querySelector("span");
    feedLabel.textContent = hasImage ? displayLive ? "Live camera" : cameraOnline ? "Stale frame" : "Disconnected" : pendingCaptureSeq != null ? "Receiving frame" : status.label === "Live camera" ? "Waiting for image" : status.label;
    $("feed-status").className = `feed-status ${hasImage ? displayLive ? "live" : "stale" : ""}`;
    const shownAge = displayedCapturedAt == null ? null : shown.age;
    text("capture-age", ageLabel(shownAge));
    $("frame-notice").hidden = !hasImage || displayLive;
    $("frame-notice").querySelector("span").textContent = cameraOnline ? "Last available frame · feed delayed" : "Last available frame · disconnected";
    // One immutable JPEG request at a time. A slow completed frame can still be
    // useful even when metadata has advanced; it retains its own capture age.
    const canLoad = status.fresh || (!hasImage && status.age != null);
    if (cameraOnline && canLoad && camera.capture_seq != null && camera.capture_seq !== lastCaptureSeq && pendingCaptureSeq == null) {
      const seq = camera.capture_seq;
      const capturedAt = timestamp(camera.captured_at);
      pendingCaptureSeq = seq;
      const incoming = new Image();
      incoming.onload = () => {
        if (pendingCaptureSeq !== seq) return;
        $("game-frame").src = incoming.src;
        $("game-frame").hidden = false;
        $("feed-empty").hidden = true;
        lastCaptureSeq = seq;
        displayedCapturedAt = capturedAt;
        pendingCaptureSeq = null;
        updateCapture();
      };
      incoming.onerror = () => { if (pendingCaptureSeq === seq) pendingCaptureSeq = null; };
      incoming.src = `/api/frame.jpg?seq=${encodeURIComponent(seq)}`;
    }
    if ($("game-frame").hidden) {
      text("empty-title", !cameraOnline ? "The feed is reconnecting." : status.age != null && !status.fresh ? "Waiting for a fresh frame." : "Waiting for a first look.");
      text("empty-description", !cameraOnline ? "The observer will reconnect automatically. Saved evidence remains available below." : "The game window will appear here when the capture feed connects.");
    }
    renderVideo();
  }
  function trackVideoFrame(_now, metadata) {
    if (videoLastMediaTime == null || metadata.mediaTime > videoLastMediaTime) {
      videoLastFrameAt = Date.now();
      videoLastMediaTime = metadata.mediaTime;
      videoFrameSamples.push({time: videoLastFrameAt, frames: metadata.presentedFrames});
      while (videoFrameSamples.length > 2 && videoLastFrameAt - videoFrameSamples[0].time > 2000) videoFrameSamples.shift();
    }
    $("game-video").requestVideoFrameCallback(trackVideoFrame);
  }
  function playbackSnapshot() {
    const video = $("game-video");
    let behind = null;
    try { if (video.seekable?.length) behind = Math.max(0, video.seekable.end(video.seekable.length - 1) - video.currentTime); } catch { /* A playlist may be switching generation. */ }
    let playingAt = null;
    try { const date = videoHls?.playingDate; if (date && Number.isFinite(date.getTime())) playingAt = date.getTime(); } catch { /* Program date time may not be available yet. */ }
    return {reachable: videoReachable, paused: video.paused !== false, last_frame_at: videoLastFrameAt, playing_at: playingAt, behind_live_seconds: behind};
  }
  function renderVideo() {
    const video = $("game-video");
    const playback = playbackSnapshot();
    const status = videoPlaybackState(videoMetadata, playback);
    const fallback = !$("game-frame").hidden;
    const hasVideo = videoLastFrameAt != null;
    // Keep a playing element composited while it buffers: hiding it can stop
    // requestVideoFrameCallback and prevent a recovered stream being observed.
    video.hidden = videoSupported === false || !videoGeneration || (video.paused !== false && fallback) || (!hasVideo && !(video.readyState >= 2));
    const captureTime = timestamp(videoMetadata?.captured_at);
    const available = videoReachable && videoMetadata?.status === "live" && captureTime != null && Date.now() - captureTime < FRESH_FRAME_MS;
    $("play-video").hidden = !available || !videoMetadata?.playlist_url || videoSupported === false || video.paused === false;
    if (video.hidden) {
      text("video-performance", fallback ? "JPEG fallback" : "Waiting for video");
      return;
    }
    $("feed-empty").hidden = true;
    const label = $("feed-status").querySelector("span");
    label.textContent = status.fresh ? "Live video" : "Video delayed";
    $("feed-status").className = `feed-status ${status.fresh ? "live" : "stale"}`;
    $("frame-notice").hidden = status.fresh;
    $("frame-notice").querySelector("span").textContent = "Last video frame · playback delayed";
    const first = videoFrameSamples[0];
    const last = videoFrameSamples.at(-1);
    const fps = first && last && last.time - first.time >= 500 ? (last.frames - first.frames) * 1000 / (last.time - first.time) : null;
    const target = Number.isFinite(videoMetadata?.target_fps) ? ` / ${videoMetadata.target_fps} target` : "";
    const rate = status.fresh && fps != null && Number.isFinite(fps) ? `${fps.toFixed(1)} FPS${target}` : status.fresh ? videoFrameCallbackStarted ? "Measuring FPS" : "FPS unavailable" : "Playback delayed";
    const latency = status.playedAge != null && status.playedAge >= 0 ? ` · ≈${(status.playedAge / 1000).toFixed(1)}s behind` : "";
    text("video-performance", `${rate}${latency}`);
    text("capture-age", status.playedAge != null ? ageLabel(Math.max(0, status.playedAge)) : ageLabel(status.captureAge == null ? null : Math.max(0, status.captureAge)));
  }
  async function playVideo() {
    try { await $("game-video").play(); } catch { $("play-video").hidden = false; }
  }
  function startVideo(metadata) {
    const video = $("game-video");
    if (!metadata?.playlist_url || Date.now() < videoRetryAt || videoGeneration === metadata.generation) return;
    videoHls?.destroy(); videoHls = null;
    videoGeneration = metadata.generation;
    videoLastFrameAt = videoLastMediaTime = null;
    videoFrameSamples = [];
    video.muted = true;
    if (!videoFrameCallbackStarted && typeof video.requestVideoFrameCallback === "function") {
      videoFrameCallbackStarted = true;
      video.requestVideoFrameCallback(trackVideoFrame);
    }
    const Hls = globalThis.Hls;
    if (Hls?.isSupported()) {
      videoSupported = true;
      videoHls = new Hls({enableWorker: true, lowLatencyMode: true, liveSyncDurationCount: 2, liveMaxLatencyDurationCount: 6, backBufferLength: 3, maxBufferLength: 4, maxMaxBufferLength: 6});
      videoHls.on(Hls.Events.MANIFEST_PARSED, playVideo);
      videoHls.on(Hls.Events.ERROR, (_event, data) => {
        if (!data.fatal) return;
        videoHls?.destroy(); videoHls = null; videoGeneration = null; videoRetryAt = Date.now() + 5000;
      });
      videoHls.loadSource(metadata.playlist_url);
      videoHls.attachMedia(video);
    } else if (video.canPlayType?.("application/vnd.apple.mpegurl")) {
      videoSupported = true; video.src = metadata.playlist_url; playVideo();
    } else {
      videoSupported = false;
    }
  }
  function renderState() {
    const state = envelope?.state || {};
    const mode = state.mode;
    const telemetry = telemetryState(envelope, Date.now(), reachable);
    renderControls(state, telemetry);
    const label = !telemetry.fresh ? telemetry.label : ({running: "Agent running", paused: "Agent paused", error: "Needs attention", idle: "Agent standing by", finished: "Run finished", completed: "Run complete"})[state.status] || "Waiting for agent";
    setStatus($("agent-status"), label, telemetry.fresh ? state.status || "neutral" : "neutral");
    text("run-context", state.run_id ? `${mode === "demo" ? "SIMULATION · " : ""}${state.run_id}` : "No run connected");
    text("objective", state.objective || "Ready when the agent is.");
    text("strategy", state.strategy || "The current plan will appear once a run begins.");
    text("expected-effect", state.expected_effect || "Waiting for a decision.");
    const metrics = state.metrics || {};
    text("elapsed", state.run_id ? elapsed(state.elapsed_ms, true) : "—");
    ["actions", "recoveries", "errors"].forEach((key) => text(key, state.run_id && metrics[key] != null ? count(metrics[key]) : "—"));
    const hasTokens = metrics.input_tokens != null || metrics.output_tokens != null;
    text("tokens", state.run_id && hasTokens ? count(n(metrics.input_tokens) + n(metrics.output_tokens)) : "—");
    text("token-detail", hasTokens && state.run_id ? `${count(metrics.input_tokens)} in / ${count(metrics.output_tokens)} out` : "Reported API usage");
    text("holo-latency", latency(state.holo_latency_ms));
    text("jev-latency", latency(state.jev_latency_ms));
    renderResources(state, telemetry);
    const action = telemetry.fresh ? state.action || {} : {};
    const held = new Set(telemetry.fresh && state.status === "running" ? (state.keys || []) : []);
    if (telemetry.fresh && state.status === "running" && action.cursor?.button === "left") held.add("attack");
    if (telemetry.fresh && state.status === "running" && action.cursor?.button === "right") held.add("use");
    document.querySelectorAll("[data-key]").forEach((element) => element.classList.toggle("active", held.has(element.dataset.key)));
    text("input-caption", !telemetry.fresh ? "Unavailable" : held.size ? action.skill ? "Skill controls" : `${action.duration_ticks || "—"} ticks` : "Idle");
    text("look-input", action.yaw_delta || action.pitch_delta ? `Yaw ${n(action.yaw_delta)}° / pitch ${n(action.pitch_delta)}°` : action.skill ? "Camera motion not reported" : "No camera input");
    const extras = [...held].filter((key) => !["forward", "back", "left", "right", "jump", "sneak", "sprint", "attack", "use"].includes(key));
    if (action.hotbar) extras.push(`Hotbar ${action.hotbar}`);
    text("extra-input", extras.length ? extras.join(" · ") : held.size ? `${held.size} active input${held.size === 1 ? "" : "s"}` : "No held controls");
    const claims = Array.isArray(state.milestones) ? state.milestones : [];
    text("milestone-count", `${claims.filter((claim) => MILESTONES[claim.id]).length} / ${Object.keys(MILESTONES).length} milestones`);
    document.querySelectorAll("[data-milestone]").forEach((element) => {
      const claim = claims.find((item) => item.id === element.dataset.milestone);
      element.classList.toggle("claimed", Boolean(claim));
      element.querySelector("div > span").textContent = claim ? `${elapsed(claim.elapsed_ms)} · ${milestoneLabel(claim, mode)}` : "Not recorded";
      element.title = evidenceText(claim?.evidence);
    });
    if (mode === "demo") text("capture-source", "Game window · agent in simulation mode");
    else text("capture-source", envelope?.source === "minecraft-window" ? "Minecraft window" : "Game window");
    updateCapture();
    renderRuns();
    if (followLive && state.run_id && selectedRun !== state.run_id) selectRun(state.run_id, true);
    else if (!selectedRun && envelope?.runs?.length) selectRun(envelope.runs[0].id, followLive);
    if (selectedRun === state.run_id && Array.isArray(state.events)) {
      const signature = state.events.map((event, index) => eventId(event, index)).join("|");
      if (signature !== lastLiveEventSignature) {
        lastLiveEventSignature = signature;
        journal.events = mergeEvents(journal.events, state.events);
        renderJournal();
      }
      if (Date.now() - journalLastFetched > JOURNAL_REFRESH_MS) fetchJournal();
    }
  }
  function renderControls(state = envelope?.state || {}, telemetry = telemetryState(envelope, Date.now(), reachable)) {
    const pending = commandBusy || operatorCommand?.status === "pending";
    const availability = controlAvailability(state, telemetry.fresh, Boolean(operatorToken), pending);
    for (const action of ["start_new", "resume", "pause", "stop"]) $(`control-${action}`).disabled = !availability[action];
    text("operator-status", operatorToken ? "Operator access" : "View only");
    $("operator-status").classList.toggle("unlocked", Boolean(operatorToken));
    $("lock-controls").hidden = !operatorToken;
    let feedback = commandMessage;
    if (operatorCommand?.status === "pending" && operatorCommand.expires_at) {
      const remaining = Math.max(0, Math.ceil((Date.parse(operatorCommand.expires_at) - Date.now()) / 1000));
      feedback = remaining ? `Command accepted · waiting for the local agent (${remaining}s remaining).` : "Waiting for the final command result…";
    }
    text("control-feedback", feedback || (!operatorToken ? "Operator access is required to control runs." : !telemetry.fresh ? "Waiting for fresh agent telemetry." : "New recordings use the current Minecraft world. Saved runs below are view-only."));
    if (operatorToken && operatorCommand?.status === "pending" && !commandBusy && !commandChecking && Date.now() - commandLastChecked >= 1000) refreshCommand();
  }
  function lockControls(message = "Controls locked. Public viewing remains available.") {
    operatorToken = "";
    try { storage?.removeItem("holospeed.operator"); storage?.removeItem("holospeed.command"); } catch { /* Memory is cleared regardless. */ }
    commandMessage = message;
    operatorCommand = null;
    renderControls();
  }
  async function commandRequest(path, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(path, {method: body ? "POST" : "GET", cache: "no-store", signal: controller.signal,
        headers: {Authorization: `Bearer ${operatorToken}`, ...(body ? {"Content-Type": "application/json"} : {})}, ...(body ? {body: JSON.stringify(body)} : {})});
      const result = await response.json();
      if (!response.ok) {
        const error = new Error(result.error || `Command request failed (${response.status}).`);
        error.status = response.status;
        throw error;
      }
      return result.command;
    } finally { clearTimeout(timer); }
  }
  async function submitCommand(action) {
    if ($(`control-${action}`).disabled || !operatorToken || commandBusy) return;
    const id = crypto.randomUUID();
    operatorCommand = {id, action, status: "pending", submitted_at: Date.now()};
    try { storage?.setItem("holospeed.command", JSON.stringify({id, submitted_at: operatorCommand.submitted_at})); } catch { /* Still track within this page. */ }
    commandBusy = true; commandMessage = "Sending command…"; renderControls();
    const submittedAt = operatorCommand.submitted_at;
    const submittedToken = operatorToken;
    try {
      const result = await commandRequest("/api/commands", {id, action});
      if (operatorToken !== submittedToken || operatorCommand?.id !== id) return;
      operatorCommand = {...result, submitted_at: submittedAt};
      commandMessage = operatorCommand.status === "pending" ? "Command accepted; waiting for the local agent." : operatorCommand.detail || `Command ${operatorCommand.status}.`;
    } catch (error) {
      if (operatorToken !== submittedToken || operatorCommand?.id !== id) return;
      if (error.status === 401 || error.status === 503) lockControls("Operator access is unavailable. Open a current private operator link.");
      else if (error.status && error.status < 500) { operatorCommand = null; commandMessage = error.message; }
      else commandMessage = "Acceptance is unconfirmed. Checking this command ID; it will not be resubmitted.";
    } finally { commandBusy = false; renderControls(); }
  }
  async function refreshCommand() {
    if (!operatorCommand || !operatorToken || commandChecking) return;
    const id = operatorCommand.id;
    commandChecking = true; commandLastChecked = Date.now();
    try {
      const result = await commandRequest(`/api/commands/${encodeURIComponent(id)}`);
      if (operatorCommand?.id !== id) return;
      operatorCommand = {...result, submitted_at: operatorCommand.submitted_at};
      commandMessage = result.status === "completed" ? result.detail || "Command completed." : result.status === "failed" ? `Command failed · ${result.detail || "Inspect the agent status."}` : result.status === "expired" ? "Command expired without a confirmed result. Check the current agent state." : "Command accepted; waiting for the local agent.";
    } catch (error) {
      if (error.status === 401 || error.status === 503) lockControls("Operator access is unavailable. Open a current private operator link.");
      else if (error.status === 404 && operatorCommand?.id === id && Date.now() - n(operatorCommand.submitted_at) > 45000) {
        operatorCommand = null; commandMessage = "No command acceptance was confirmed. Check the current agent state before retrying.";
      } else commandMessage = "Command result is unavailable; checking again automatically.";
    } finally { commandChecking = false; renderControls(); }
  }
  function renderResources(state, telemetry) {
    // Keep only the latest evidence of each kind, scoped to the current run.
    // Archive selection never replaces the live game's resource panel.
    if (evidenceRun !== state.run_id) { evidenceRun = state.run_id; resourceEvidence = []; }
    const types = ["state_summary", "skill_progress", "skill_started", "skill_decision"];
    const available = state.run_id ? [...resourceEvidence, ...(Array.isArray(state.events) ? state.events : []), ...(selectedRun === state.run_id ? journal.events : [])] : [];
    resourceEvidence = types.map((type) => latestEvent(available, type)).filter(Boolean);
    const sample = latestEvent(resourceEvidence, "state_summary");
    const skill = skillTelemetry(state.action, resourceEvidence);
    const active = telemetry.fresh && state.status === "running";
    text("skill-state", skill ? active ? "Current skill" : "Last reported skill" : "Current skill");
    text("skill-name", skill ? `${skill.name} · ${skill.item}` : "No active skill recorded");
    text("skill-count", skill ? `${skill.count == null ? "—" : count(skill.count)} / ${skill.target == null ? "—" : count(skill.target)}` : "—");
    text("skill-phase", skill ? `${skill.phase || "Awaiting progress"}${skill.event ? ` · sampled ${elapsed(skill.event.elapsed_ms)}` : ""}` : "Skill progress will appear during a state-aware run.");
    text("resource-stamp", sample ? `${telemetry.fresh ? "Recorded" : "Last recorded"} ${elapsed(sample.elapsed_ms)} · tick ${sample.data.tick ?? "—"}` : "No state sample");
    const data = sample?.data || {};
    const player = data.player || {};
    const finite = (value) => typeof value === "number" && Number.isFinite(value);
    text("player-health", finite(player.health) ? `${player.health} / 20` : "—");
    text("player-hunger", finite(player.hunger) ? `${player.hunger} / 20` : "—");
    const position = player.position;
    text("player-position", position && [position.x, position.y, position.z].every(finite) ? [position.x, position.y, position.z].map((value) => value.toFixed(1)).join(" / ") : "—");
    const recorded = data.inventory && typeof data.inventory === "object" && !Array.isArray(data.inventory);
    const items = recorded ? Object.entries(data.inventory).filter(([, value]) => finite(value) && value > 0).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])) : [];
    const chips = items.slice(0, 12).map(([item, value]) => {
      const chip = document.createElement("span"); chip.className = "resource-chip"; chip.textContent = `${itemName(item)} · ${count(value)}`; chip.title = item; return chip;
    });
    $("inventory-items").replaceChildren(...chips);
    text("inventory-note", !recorded ? "Inventory not recorded for this run." : !items.length ? "Inventory empty at this sample." : items.length > 12 ? `${items.length - 12} more item types in the journal. Sampled at skill boundaries.` : "Inventory and player status are sampled at skill boundaries.");
  }
  function renderRuns() {
    const runs = envelope?.runs || [];
    const signature = JSON.stringify(runs.map((run) => [run.id, run.mode, run.status, run.duration_ms, run.metrics?.actions])) + selectedRun;
    if (signature === lastRunListSignature) return;
    lastRunListSignature = signature;
    text("runs-count", runs.length);
    const fragment = document.createDocumentFragment();
    if (!runs.length) {
      const empty = document.createElement("p"); empty.className = "empty-list"; empty.textContent = "Saved runs will appear here."; fragment.append(empty);
    }
    for (const run of runs) {
      const button = document.createElement("button"); button.type = "button"; button.className = `run-item${selectedRun === run.id ? " selected" : ""}`; button.setAttribute("aria-pressed", String(selectedRun === run.id));
      const top = document.createElement("span"); top.className = "run-item-top";
      const date = document.createElement("span"); const parsed = timestamp(run.started_at); date.textContent = parsed == null ? "Recorded run" : new Date(parsed).toLocaleString([], {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"});
      const mode = document.createElement("span"); mode.className = "run-mode"; mode.textContent = run.mode === "demo" ? "DEMO" : run.mode === "live" ? "LIVE" : "RUN"; top.append(date, mode);
      const id = document.createElement("div"); id.className = "run-item-id"; id.textContent = run.id;
      const meta = document.createElement("span"); meta.className = "run-item-meta";
      const status = document.createElement("span"); status.className = "run-item-state"; if (["running", "error"].includes(run.status)) status.classList.add(run.status); status.textContent = run.status || "recorded";
      const duration = document.createElement("span"); duration.textContent = `· ${elapsed(run.duration_ms)}`;
      const actions = document.createElement("span"); actions.textContent = run.metrics?.actions != null ? `· ${count(run.metrics.actions)} actions` : "";
      meta.append(status, duration, actions); button.append(top, id, meta); button.addEventListener("click", () => selectRun(run.id, false)); fragment.append(button);
    }
    $("runs-list").replaceChildren(fragment);
  }
  function selectRun(id, follow) {
    followLive = follow;
    $("follow-live").setAttribute("aria-pressed", String(followLive));
    if (selectedRun === id) { if (!journalLoaded) fetchJournal(); return; }
    selectedRun = id;
    journal = {summary: (envelope?.runs || []).find((run) => run.id === id) || null, events: []};
    journalLoaded = false; journalBusy = false; journalLastFetched = 0; journalRequestVersion++; shownEvents = 150; lastLiveEventSignature = ""; expandedEvents.clear();
    $("journal-alert").hidden = true; $("event-search").value = ""; $("event-type").value = "all";
    renderRuns(); renderJournal(); fetchJournal();
  }
  async function fetchJournal() {
    if (!selectedRun || journalBusy) return;
    journalBusy = true;
    const runId = selectedRun;
    const version = journalRequestVersion;
    text("journal-load-status", journalLoaded ? "Refreshing journal…" : "Loading full journal…");
    try {
      const result = await get(`/api/runs/${encodeURIComponent(runId)}`);
      if (selectedRun !== runId || journalRequestVersion !== version) return;
      journal.summary = result.summary || journal.summary;
      journal.events = mergeEvents(journal.events, Array.isArray(result.events) ? result.events : []);
      journalLoaded = true; journalLastFetched = Date.now(); $("journal-alert").hidden = true;
      renderJournal();
    } catch (error) {
      if (selectedRun !== runId || journalRequestVersion !== version) return;
      journalLastFetched = Date.now();
      text("journal-alert", `${error.name === "AbortError" ? "The journal request timed out." : error.message} ${journal.events.length ? "Available events are still shown." : selectedRun === envelope?.state?.run_id ? "The next live refresh will retry." : "Select this run again to retry."}`);
      $("journal-alert").hidden = false; text("journal-load-status", journal.events.length ? "Partial journal" : "Journal unavailable");
    } finally {
      if (journalRequestVersion === version) journalBusy = false;
    }
  }
  function renderJournal() {
    const summary = journal.summary;
    text("journal-run-title", selectedRun || "Waiting for a run");
    text("journal-mode", selectedRun ? summary?.mode === "demo" ? "SIMULATION · RECORDED EVIDENCE" : "LIVE GAME · RECORDED EVIDENCE" : "NO RUN SELECTED");
    text("journal-load-status", journalLoaded ? `${count(journal.events.length)} recorded events` : journal.events.length ? "Recent events · loading history" : selectedRun ? "Loading full journal…" : "No events yet");
    text("journal-scope", selectedRun === envelope?.state?.run_id ? "Current run · updates automatically" : "Archived run");
    $("download-journal").disabled = !journalLoaded;
    const selectedType = $("event-type").value;
    const types = [...new Set(journal.events.map((event) => event.type).filter(Boolean))].sort();
    const typeSignature = types.join("|");
    if ($("event-type").dataset.types !== typeSignature) {
      $("event-type").dataset.types = typeSignature;
      const all = document.createElement("option"); all.value = "all"; all.textContent = "All event types";
      const options = types.map((type) => {const option = document.createElement("option"); option.value = type; option.textContent = eventTypeLabel(type); return option;});
      $("event-type").replaceChildren(all, ...options);
      $("event-type").value = types.includes(selectedType) ? selectedType : "all";
    }
    const filterType = $("event-type").value;
    const query = $("event-search").value.toLowerCase().trim();
    const filtered = journal.events.filter((event) => (filterType === "all" || event.type === filterType) && (!query || JSON.stringify(event).toLowerCase().includes(query))).reverse();
    text("filtered-count", `${count(filtered.length)} event${filtered.length === 1 ? "" : "s"}`);
    const fragment = document.createDocumentFragment();
    if (!filtered.length) {
      const empty = document.createElement("div"); empty.className = "journal-empty";
      const symbol = document.createElement("span"); symbol.className = "journal-empty-mark"; symbol.textContent = "[ ]";
      const title = document.createElement("h4"); title.textContent = query || filterType !== "all" ? "No matching events." : "Nothing to inspect yet.";
      const message = document.createElement("p"); message.textContent = query || filterType !== "all" ? "Try another search or select all event types." : selectedRun ? "The run’s recorded events will appear here." : "Choose a saved run or follow the current session.";
      empty.append(symbol, title, message); fragment.append(empty);
    }
    filtered.slice(0, shownEvents).forEach((event, index) => {
      const key = `${selectedRun}:${eventId(event, index)}`;
      const details = document.createElement("details"); details.className = "event-row"; details.open = expandedEvents.has(key);
      const heading = document.createElement("summary");
      const stamp = document.createElement("span"); stamp.className = "event-time"; stamp.textContent = elapsed(event.elapsed_ms);
      const type = document.createElement("span"); type.className = "event-type"; if (["decision", "plan", "error", "milestone"].includes(event.type)) type.classList.add(event.type); type.textContent = eventTypeLabel(event.type); type.title = String(event.type || "event");
      const description = document.createElement("span"); description.className = "event-description"; description.textContent = eventDescription(event);
      const arrow = document.createElement("span"); arrow.className = "event-chevron"; arrow.setAttribute("aria-hidden", "true");
      heading.append(stamp, type, description, arrow); details.append(heading);
      function ensureJSON() {if (!details.querySelector("pre")) {const pre = document.createElement("pre"); pre.className = "event-json"; pre.textContent = JSON.stringify(event, null, 2); details.append(pre);}}
      if (details.open) ensureJSON();
      details.addEventListener("toggle", () => {if (details.open) {expandedEvents.add(key); ensureJSON();} else expandedEvents.delete(key);});
      fragment.append(details);
    });
    const previousScroll = $("event-list").scrollTop;
    $("event-list").replaceChildren(fragment);
    $("event-list").scrollTop = previousScroll;
    $("load-more").hidden = filtered.length <= shownEvents;
    text("load-more", `Show ${Math.min(150, Math.max(0, filtered.length - shownEvents))} more events`);
  }
  async function poll() {
    try {
      const result = await get("/api/state");
      if (!result || typeof result !== "object") throw new Error("Invalid state response");
      envelope = result; reachable = true; renderState();
    } catch {
      reachable = false; renderState();
    } finally { setTimeout(poll, 1000); }
  }
  async function pollCapture() {
    const started = Date.now();
    try {
      const result = await get("/api/capture", 3500);
      if (!result || typeof result !== "object") throw new Error("Invalid capture metadata");
      if (!cameraEnvelope || result.capture_seq == null || result.capture_seq >= cameraEnvelope.capture_seq) cameraEnvelope = result;
      cameraReachable = true; updateCapture();
    } catch { cameraReachable = false; updateCapture(); }
    finally { setTimeout(pollCapture, Math.max(0, (document.hidden ? 2000 : cameraReachable ? CAMERA_INTERVAL_MS : 1000) - (Date.now() - started))); }
  }
  async function pollVideo() {
    try {
      const metadata = await get("/api/live/status", 3500);
      // Same-origin fixed path only; a returned URL cannot redirect media to a
      // third party or expose the operator capability.
      if (metadata?.playlist_url && metadata.playlist_url !== "/api/live/index.m3u8") throw new Error("Invalid video source");
      videoMetadata = metadata; videoReachable = true;
      if (metadata?.status === "live") startVideo(metadata);
      updateCapture();
    } catch { videoReachable = false; updateCapture(); }
    finally { setTimeout(pollVideo, document.hidden ? 2000 : 1000); }
  }

  $("fullscreen-button").addEventListener("click", async () => {
    try {if (document.fullscreenElement) await document.exitFullscreen(); else if ($("feed-stage").requestFullscreen) await $("feed-stage").requestFullscreen(); else notify("Fullscreen is not available in this browser.");}
    catch {notify("Fullscreen could not be opened. You can still watch the feed here.");}
  });
  $("follow-live").addEventListener("click", () => {
    followLive = true; $("follow-live").setAttribute("aria-pressed", "true");
    if (envelope?.state?.run_id) selectRun(envelope.state.run_id, true);
    else notify("The next live run will be selected automatically.");
  });
  ["start_new", "resume", "pause", "stop"].forEach((action) => $(`control-${action}`).addEventListener("click", () => submitCommand(action)));
  $("lock-controls").addEventListener("click", () => lockControls("Controls locked. An already accepted command may still finish."));
  $("play-video").addEventListener("click", playVideo);
  $("game-video").addEventListener("timeupdate", () => {
    // Older native-HLS browsers can report playback progress without claiming
    // an FPS measurement when frame callbacks are unavailable.
    const video = $("game-video");
    if (!videoFrameCallbackStarted && video.currentTime > (videoLastMediaTime ?? -1)) { videoLastMediaTime = video.currentTime; videoLastFrameAt = Date.now(); }
  });
  $("event-search").addEventListener("input", () => {clearTimeout(searchTimer); searchTimer = setTimeout(() => {shownEvents = 150; renderJournal();}, 120);});
  $("event-type").addEventListener("change", () => {shownEvents = 150; renderJournal();});
  $("load-more").addEventListener("click", () => {shownEvents += 150; renderJournal();});
  $("download-journal").addEventListener("click", () => {
    if (!journalLoaded || !selectedRun) return;
    const blob = new Blob([JSON.stringify({summary: journal.summary, events: journal.events}, null, 2)], {type: "application/json"});
    const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = `holospeed-${selectedRun}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  setInterval(updateCapture, 250);
  setTimeout(pollCapture, 0);
  setTimeout(pollVideo, 0);
  poll();
})();
