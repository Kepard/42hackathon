(function () {
  "use strict";

  const FRESH_FRAME_MS = 15000;
  const CAMERA_INTERVAL_MS = 1000; // JPEG fallback; HLS is the primary video feed.
  const JOURNAL_REFRESH_MS = 5000;
  const MILESTONES = {
    wood: "Gather wood", crafting_table: "Craft a table", wooden_pickaxe: "Make a pickaxe", stone_tools: "Reach stone tools",
    stone_pickaxe: "Make a stone pickaxe", furnace: "Make a furnace", iron_mined: "Mine iron", iron_smelted: "Smelt iron",
    bucket: "Make a bucket", flint_and_steel: "Acquire flint and steel", water_collected: "Collect water", lava_located: "Locate lava", portal_built: "Build the portal frame",
    portal_located: "Locate the portal", chest_looted: "Loot the portal chest", obsidian_acquired: "Acquire obsidian",
    ignition_acquired: "Acquire ignition", portal_repaired: "Repair the portal", portal_lit: "Light the portal", nether: "Nether entry"
  };
  const ROUTES = {
    from_scratch: {
      label: "From scratch to the Nether", preparation: "BUILD THE KIT", journey: "MINE · SMELT · CAST", caption: "Survival resources. A new portal.",
      groups: [
        [["wood", "Wood", "log"], ["crafting_table", "Workbench", "table"], ["wooden_pickaxe", "Wood pick", "pickaxe"], ["stone_pickaxe", "Stone pick", "stone-pickaxe"], ["furnace", "Furnace", "furnace"]],
        [["iron_mined", "Mine iron", "iron-ore"], ["iron_smelted", "Smelt iron", "iron"], ["bucket", "Bucket", "bucket"], ["flint_and_steel", "Flint & steel", "igniter"], ["water_collected", "Water", "water-bucket"], ["lava_located", "Lava", "lava"], ["portal_built", "Cast frame", "frame"], ["portal_lit", "Light portal", "lit-portal"], ["nether", "Enter Nether", "portal"]]
      ]
    },
    ruined_portal: {
      label: "Ruined portal route", preparation: "PREPARATION", journey: "NETHER ROUTE", caption: "Locate. Repair. Cross.",
      groups: [
        [["wood", "Wood", "log"], ["crafting_table", "Crafting table", "table"], ["wooden_pickaxe", "Wooden pickaxe", "pickaxe"], ["stone_tools", "Stone tools", "stone-pickaxe"]],
        [["portal_located", "Locate portal", "locate"], ["chest_looted", "Loot chest", "chest"], ["obsidian_acquired", "Obsidian", "obsidian"], ["ignition_acquired", "Ignition", "fire"], ["portal_repaired", "Repair frame", "frame"], ["portal_lit", "Light portal", "lit-portal"], ["nether", "Enter Nether", "portal"]]
      ]
    }
  };
  function routeProgress(state = {}) {
    // Existing runs without route metadata retain their original checkpoints.
    const key = Object.hasOwn(ROUTES, state.route) ? state.route : state.run_id ? "ruined_portal" : "from_scratch";
    const route = ROUTES[key];
    const ids = route.groups.flat().map(([id]) => id);
    const claims = Array.isArray(state.milestones) ? state.milestones : [];
    const recorded = new Set(claims.map((claim) => claim.id).filter((id) => ids.includes(id)));
    return {key, ...route, ids, completed: recorded.size};
  }
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
  function concise(value, words = 20) {
    const parts = String(value || "").trim().split(/\s+/).filter(Boolean);
    return parts.length > words ? `${parts.slice(0, words).join(" ")}…` : parts.join(" ");
  }
  function inferenceStage(events, provider, state, fresh, now = Date.now()) {
    const calls = events.filter((event) => event.type === "inference" && event.data?.provider === provider && event.data?.decision_role !== "voice_response");
    const latest = latestEvent(calls, "inference");
    const result = latestEvent(calls.filter((event) => event.data?.phase === "complete"), "inference");
    const failed = latestEvent(calls.filter((event) => event.data?.phase === "failed"), "inference");
    const stamp = timestamp(latest?.timestamp);
    const age = stamp == null ? null : now - stamp;
    const pending = latest?.data?.phase === "thinking";
    // A heartbeat cannot keep an abandoned request looking active indefinitely.
    const active = Boolean(fresh && state?.status === "running" && pending && age != null && age >= -2000 && age < 15000);
    const status = active ? "Thinking" : !fresh && latest ? "Last recorded" : pending ? "No result recorded" : latest?.data?.phase === "failed" ? latest.data.cancelled ? "Cancelled" : latest.data.rejected ? "Action rejected" : "Last call failed" : latest?.data?.phase === "complete" ? "Decision recorded" : "Standby";
    return {latest, result, failed, active, status, task: concise(latest?.data?.task || ""), rationale: concise(result?.data?.rationale || ""), inputSource: latest?.data?.input_source || null};
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
    if (event.type === "voice_received") return `${data.source === "gladia" ? "Gladia transcript" : "Operator message"} · ${data.text || "Instruction recorded"}`;
    if (event.type === "voice_reply") return `${data.provider === "holo" ? "Holo4" : "Agent"} · ${data.reply || "Reply recorded"}`;
    if (event.type === "voice_guidance") return `Objective guidance · ${data.text || "Guidance recorded"}`;
    if (event.type === "inference") return `${providerName(data.provider)} · ${data.phase || "request"}${data.task ? ` · ${data.task}` : ""}${data.rationale ? ` · ${data.rationale}` : data.reason ? ` · ${data.reason}` : ""}`;
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
    const preparing = state?.launch?.status === "preparing";
    const ready = Boolean(unlocked && fresh && !pending && (preparing || state?.status !== "unavailable"));
    return {
      start_new: ready && !preparing && Object.hasOwn(state || {}, "launch") && state?.connected === true,
      resume: ready && !preparing && state?.connected === true && Boolean(state?.run_id) && ["paused", "error"].includes(state?.status),
      pause: ready && (preparing || state?.status === "running"),
      stop: ready && (preparing || Boolean(state?.run_id)),
    };
  }
  function launchProgress(value) {
    if (!value || !["preparing", "running", "failed", "cancelled", "interrupted"].includes(value.status)) return null;
    const stages = ["saving", "copying", "loading", "verifying", "starting"];
    const labels = {saving: "Saving the previous world", copying: "Copying a clean world", loading: "Loading Minecraft", verifying: "Verifying the fresh spawn", starting: "Starting the agent", running: "Fresh run started", failed: "Launch failed", cancelled: "Launch cancelled", interrupted: "Launch interrupted"};
    const active = value.status === "preparing";
    const stage = active && stages.includes(value.stage) ? value.stage : active ? null : value.status;
    return {active, status: value.status, stage, index: stages.indexOf(stage), label: labels[stage] || "Preparing a fresh run", detail: typeof value.detail === "string" ? value.detail : "", world: typeof value.world_name === "string" ? value.world_name : "", runId: typeof value.run_id === "string" ? value.run_id : null};
  }

  function investigationPreview(value, runId) {
    if (!value || value.run_id !== runId || typeof value.prompt !== "string" || !value.prompt.trim() || value.prompt.length > 100000 || !/^[a-f0-9]{64}$/.test(value.digest_hash || "") || value.max_acu_limit !== 2 || typeof value.can_submit !== "boolean") return null;
    return {run_id: value.run_id, prompt: value.prompt, digest_hash: value.digest_hash, max_acu_limit: 2, can_submit: value.can_submit && !value.existing, existing: value.existing || null};
  }
  function engineeringStatus(value, now = Date.now()) {
    if (!value) return "No investigation yet";
    if (value.status_detail === "finished" && ["running", "exit"].includes(value.status)) return "Investigation finished";
    if (value.status_detail === "waiting_for_user") return "Waiting for operator input";
    const recordedAt = timestamp(value.updated_at);
    const ongoing = {submitting: "submitting", new: "queued", created: "session created", claimed: "assigned", running: "running", resuming: "resuming"};
    if (recordedAt != null && now - recordedAt > 60000 && ongoing[value.status]) return `Last recorded: ${ongoing[value.status]}`;
    return ({submitting: "Submitting to Devin", unknown: "Submission unconfirmed", failed: "Submission failed", new: "Queued with Devin", created: "Session created", claimed: "Assigned to Devin", running: "Devin is investigating", exit: "Session ended", error: "Needs attention", suspended: "Session paused", resuming: "Session resuming"})[value.status] || "Status recorded";
  }
  function devinSessionUrl(value) {
    return typeof value === "string" && /^https:\/\/app\.devin\.ai\/sessions\/[A-Za-z0-9_-]+$/.test(value) ? value : null;
  }
  // Pure helpers can be tested in Node without a browser or network.
  if (typeof module === "object" && module.exports) {
    module.exports = {timestamp, elapsed, ageLabel, latency, captureState, telemetryState, displayedFrameState, videoPlaybackState, milestoneLabel, itemName, latestEvent, skillTelemetry, eventDescription, eventTypeLabel, mergeEvents, operatorCredential, controlAvailability, concise, inferenceStage, investigationPreview, engineeringStatus, devinSessionUrl, routeProgress, launchProgress};
    return;
  }

  const $ = (id) => document.getElementById(id);
  const text = (id, value) => { const element = $(id); if (element.textContent !== String(value)) element.textContent = value; };
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
  let inferenceRun = null;
  let inferenceEvidence = [];
  let engineeringRun = null;
  let engineeringPreview = null;
  let engineeringMessage = "";
  let engineeringBusy = false;
  let engineeringLastFetched = 0;
  let engineeringSelectionVersion = 0;
  let handledEngineeringCommand = null;
  let engineeringFindingsSignature = null;
  const engineeringByRun = new Map();
  const expandedEvents = new Set();
  let storage = null;
  try { storage = typeof sessionStorage !== "undefined" ? sessionStorage : null; } catch { /* Private browsing may deny storage. */ }
  let operatorToken = operatorCredential(typeof location !== "undefined" ? location : null, typeof history !== "undefined" ? history : null, storage);
  let operatorCommand = null;
  let commandBusy = false;
  let commandChecking = false;
  let commandLastChecked = 0;
  let commandMessage = "";
  let loginBusy = false;
  let loginAttempt = 0;
  let loginMessage = "";
  let voiceSession = null;
  let voicePhase = "idle";
  let voiceRunId = null;
  let voiceStartedAt = null;
  let voicePartial = "";
  let voiceTranscript = "";
  let voiceReply = "";
  let voiceMessage = "";
  let voiceCurrentCommandId = null;
  let voiceDisplayRunId;
  let voiceSeenReceivedSeq = -1;
  let voiceRetryAt = 0;
  const voiceQueue = [];
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
    const recordedState = envelope?.state || {};
    const launch = launchProgress(recordedState.launch);
    // Preparation is not a new gameplay recording. Keep its predecessor in
    // the journal instead of presenting old progress as the freshly loaded run.
    const state = launch?.active ? {...recordedState, run_id: null, status: "preparing", action: null, keys: [], metrics: {}, milestones: [], events: [], holo_latency_ms: null, jev_latency_ms: null, objective: "Preparing a fresh Minecraft world.", strategy: launch.detail || "Copy the clean template, load it, and verify the starting state.", expected_effect: "Autonomous gameplay starts after the world is verified."} : recordedState;
    const mode = state.mode;
    const telemetry = telemetryState(envelope, Date.now(), reachable);
    renderControls(recordedState, telemetry);
    const label = !telemetry.fresh ? telemetry.label : ({running: "Agent running", preparing: "Preparing new run", paused: "Agent paused", error: "Needs attention", idle: "Agent standing by", finished: "Run finished", completed: "Run complete"})[state.status] || "Waiting for agent";
    setStatus($("agent-status"), label, telemetry.fresh ? state.status || "neutral" : "neutral");
    text("run-context", launch?.active ? "Fresh world preparation · gameplay has not started" : state.run_id ? `${mode === "demo" ? "SIMULATION · " : ""}${state.run_id}` : "No run connected");
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
    renderMission(state, telemetry);
    renderEngineering();
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
    renderMilestones(state);
    $("route-panel").hidden = Boolean(launch?.active);
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
  function renderMilestones(state) {
    const route = routeProgress(state);
    const panel = $("route-panel");
    if (panel.dataset.route !== route.key) {
      panel.dataset.route = route.key;
      route.groups.forEach((group, index) => {
        const nodes = group.map(([id, label, icon]) => {
          const item = document.createElement("li"); item.dataset.milestone = id;
          const symbol = document.createElement("span"); symbol.className = `milestone-dot item-${icon}`; symbol.setAttribute("aria-hidden", "true");
          const description = document.createElement("div");
          const name = document.createElement("strong"); name.textContent = label;
          const status = document.createElement("span"); status.textContent = "Not recorded";
          description.append(name, status); item.append(symbol, description);
          if (id === "nether") {const goal = document.createElement("b"); goal.className = "goal-tag"; goal.textContent = "GOAL"; item.append(goal);}
          return item;
        });
        $(index === 0 ? "preparation-milestones" : "milestones").replaceChildren(...nodes);
      });
    }
    text("progression-title", route.label);
    text("preparation-label", route.preparation);
    text("portal-route-label", route.journey);
    text("portal-route-caption", route.caption);
    text("milestone-count", `${route.completed} / ${route.ids.length} milestones`);
    const claims = Array.isArray(state.milestones) ? state.milestones : [];
    document.querySelectorAll("[data-milestone]").forEach((element) => {
      const claim = claims.find((item) => item.id === element.dataset.milestone);
      element.classList.toggle("claimed", Boolean(claim));
      element.querySelector("div > span").textContent = claim ? `${elapsed(claim.elapsed_ms)} · ${milestoneLabel(claim, state.mode)}` : "Not recorded";
      element.title = evidenceText(claim?.evidence);
    });
  }
  function renderControls(state = envelope?.state || {}, telemetry = telemetryState(envelope, Date.now(), reachable)) {
    const pending = commandBusy || operatorCommand?.status === "pending";
    const launchSupported = Object.hasOwn(state, "launch");
    const availability = controlAvailability(state, telemetry.fresh, Boolean(operatorToken), pending);
    for (const action of ["start_new", "resume", "pause", "stop"]) $(`control-${action}`).disabled = !availability[action];
    text("operator-status", operatorToken ? "Operator access" : "View only");
    $("operator-status").classList.toggle("unlocked", Boolean(operatorToken));
    $("lock-controls").hidden = !operatorToken;
    $("operator-unlock-link").hidden = Boolean(operatorToken);
    renderLogin();
    renderVoice();
    renderLaunch(state, telemetry);
    text("operator-world-note", !launchSupported ? "Launcher not connected" : state.connected !== true && state.launch?.status !== "preparing" ? "Waiting for Minecraft" : "Fresh world · Automatic start");
    $("control-start_new").title = launchSupported ? "Fresh world · Automatic start" : "Launcher not connected";
    let feedback = commandMessage;
    if (operatorCommand?.status === "pending" && operatorCommand.expires_at) {
      const remaining = Math.max(0, Math.ceil((Date.parse(operatorCommand.expires_at) - Date.now()) / 1000));
      feedback = remaining ? `Command accepted · waiting for the local agent (${remaining}s remaining).` : "Waiting for the final command result…";
    }
    text("control-feedback", feedback || (!operatorToken ? "Operator access is required to control runs." : !telemetry.fresh ? "Waiting for fresh agent telemetry." : !launchSupported ? "Launcher not connected. Fresh-world starts are unavailable until it connects." : state.launch?.status === "preparing" ? "Preparing a clean world. Pause or End run cancels this launch." : "New run loads a clean world copy and starts the agent. Previous runs stay in the journal."));
    if (operatorToken && operatorCommand?.status === "pending" && !commandBusy && !commandChecking && Date.now() - commandLastChecked >= 1000) refreshCommand();
  }
  function renderLaunch(state, telemetry) {
    const launch = launchProgress(state.launch);
    $("run-launch").hidden = !launch;
    $("control-pause").title = launch?.active ? "Cancel fresh-world preparation" : "Pause gameplay";
    $("control-stop").title = launch?.active ? "Cancel this launch and preserve its world" : "Stop and archive the current run";
    $("control-pause").setAttribute("aria-label", $("control-pause").title);
    $("control-stop").setAttribute("aria-label", $("control-stop").title);
    if (!launch) return;
    $("run-launch").dataset.status = launch.status;
    $("run-launch").classList.toggle("active", launch.active && telemetry.fresh);
    text("launch-heading", launch.label);
    text("launch-state", !telemetry.fresh ? "LAST RECORDED" : launch.active ? "PREPARING" : launch.status.toUpperCase());
    text("launch-detail", launch.detail || (launch.active ? "Preparing the next world. Gameplay has not started." : "This launch attempt has ended."));
    $("launch-world").hidden = !launch.world;
    text("launch-world", launch.world);
    $("launch-run").hidden = !launch.runId;
    text("launch-run", launch.runId ? `Recording ${launch.runId}` : "");
    $("launch-stages").hidden = !launch.active;
    $("launch-cancel-note").hidden = !launch.active;
    document.querySelectorAll("[data-launch-stage]").forEach((element) => {
      const current = element.dataset.launchStage === launch.stage;
      element.classList.toggle("current", current);
      if (current) element.setAttribute("aria-current", "step"); else element.removeAttribute?.("aria-current");
    });
  }
  function lockControls(message = "Controls locked. Public viewing remains available.") {
    operatorToken = "";
    voiceSession?.cancel();
    voiceQueue.length = 0;
    voicePartial = "";
    voiceMessage = "Microphone off. Unlock controls to speak or send a message.";
    loginAttempt++;
    loginBusy = false;
    loginMessage = "Controls locked. You can continue watching.";
    $("operator-password").value = "";
    try { storage?.removeItem("holospeed.operator"); storage?.removeItem("holospeed.command"); } catch { /* Memory is cleared regardless. */ }
    commandMessage = message;
    operatorCommand = null;
    engineeringPreview = null;
    $("devin-confirm").checked = false;
    renderControls();
    renderEngineering();
  }
  function renderLogin() {
    $("operator-login-form").hidden = Boolean(operatorToken);
    $("operator-footer-lock").hidden = !operatorToken;
    $("operator-password").disabled = loginBusy;
    $("operator-login-submit").disabled = loginBusy;
    text("operator-login-submit", loginBusy ? "Unlocking…" : "Unlock controls");
    text("operator-access-state", operatorToken ? "UNLOCKED IN THIS TAB" : "VIEW ONLY");
    $("operator-access-state").classList.toggle("unlocked", Boolean(operatorToken));
    text("operator-login-feedback", loginMessage || (operatorToken ? "Run controls and Devin investigations are available in this tab." : "Enter the operator password to control runs. Watching is always available."));
  }
  async function unlockControls(event) {
    event?.preventDefault();
    if (loginBusy || operatorToken) return;
    const password = $("operator-password").value;
    $("operator-password").value = "";
    if (!password || password.length > 512) {loginMessage = "Enter the operator password."; renderLogin(); return;}
    const attempt = ++loginAttempt;
    loginBusy = true; loginMessage = "Checking operator access…"; renderLogin();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch("/api/operator/login", {method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error", signal: controller.signal, headers: {"Content-Type": "application/json"}, body: JSON.stringify({password})});
      if (attempt !== loginAttempt || operatorToken) return;
      if (!response.ok) {
        loginMessage = ({400: "Enter a valid operator password.", 401: "That password did not unlock the controls.", 403: "Operator access must be requested from this dashboard.", 429: "Too many attempts. Wait a minute before trying again.", 503: "Password access is not configured yet. Please try again later."})[response.status] || "Controls could not be unlocked. Please try again.";
        return;
      }
      const result = await response.json();
      if (attempt !== loginAttempt || operatorToken) return;
      if (typeof result.token !== "string" || !/^[A-Za-z0-9_-]{32,256}$/.test(result.token)) {loginMessage = "The server did not return valid operator access."; return;}
      operatorToken = result.token;
      try {storage?.setItem("holospeed.operator", operatorToken);} catch { /* Access remains available in this page only. */ }
      loginMessage = "Controls unlocked in this tab. Choose a run action above.";
      commandMessage = "Operator access unlocked. Run controls are ready when the agent is connected.";
    } catch (error) {
      if (attempt !== loginAttempt) return;
      loginMessage = error.name === "AbortError" ? "The unlock request timed out. Please try again." : "Operator access could not be reached. Please try again.";
    } finally {
      clearTimeout(timer);
      if (attempt === loginAttempt) {loginBusy = false; renderControls(); renderEngineering();}
    }
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
  async function submitCommand(action, payload = {}) {
    const engineering = ["prepare_investigation", "investigate"].includes(action);
    if (engineering) {
      renderEngineering();
      if ($(action === "prepare_investigation" ? "devin-prepare" : "devin-submit").disabled || payload.run_id !== engineeringRun) return;
      if (action === "investigate" && (!engineeringPreview || payload.digest_hash !== engineeringPreview.digest_hash)) return;
    } else if ($(`control-${action}`).disabled) return;
    if (!operatorToken || commandBusy) return;
    const id = crypto.randomUUID();
    operatorCommand = {id, action, status: "pending", submitted_at: Date.now()};
    try { storage?.setItem("holospeed.command", JSON.stringify({id, submitted_at: operatorCommand.submitted_at})); } catch { /* Still track within this page. */ }
    commandBusy = true; commandMessage = "Sending command…";
    if (engineering) { engineeringMessage = action === "prepare_investigation" ? "Preparing the exact redacted brief…" : "Requesting one capped Devin investigation…"; renderEngineering(); }
    renderControls();
    const submittedAt = operatorCommand.submitted_at;
    const submittedToken = operatorToken;
    try {
      const result = await commandRequest("/api/commands", {id, action, ...(engineering ? payload : {})});
      if (operatorToken !== submittedToken || operatorCommand?.id !== id) return;
      operatorCommand = {...result, action: result.action || action, submitted_at: submittedAt};
      commandMessage = operatorCommand.status === "pending" ? "Command accepted; waiting for the local agent." : operatorCommand.detail || `Command ${operatorCommand.status}.`;
      handleEngineeringCommand(operatorCommand);
      handleVoiceCommand(operatorCommand);
    } catch (error) {
      if (operatorToken !== submittedToken || operatorCommand?.id !== id) return;
      if (error.status === 401 || error.status === 503) lockControls("Operator access is unavailable. Open a current private operator link.");
      else if (error.status && error.status < 500) { operatorCommand = null; commandMessage = error.message; }
      else commandMessage = "Acceptance is unconfirmed. Checking this command ID; it will not be resubmitted.";
      if (engineering) engineeringMessage = commandMessage;
    } finally { commandBusy = false; renderControls(); renderEngineering(); }
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
      handleEngineeringCommand(operatorCommand);
      handleVoiceCommand(operatorCommand);
    } catch (error) {
      if (error.status === 401 || error.status === 503) lockControls("Operator access is unavailable. Open a current private operator link.");
      else if (error.status === 404 && operatorCommand?.id === id && Date.now() - n(operatorCommand.submitted_at) > 45000) {
        operatorCommand = null; commandMessage = "No command acceptance was confirmed. Check the current agent state before retrying.";
      } else commandMessage = "Command result is unavailable; checking again automatically.";
    } finally { commandChecking = false; renderControls(); renderEngineering(); }
  }
  function voiceAvailable() {
    return Boolean(operatorToken && telemetryState(envelope, Date.now(), reachable).fresh);
  }
  async function createVoiceSession(signal) {
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal.addEventListener("abort", abort, {once: true});
    const timer = setTimeout(abort, 12000);
    const token = operatorToken;
    try {
      const response = await fetch("/api/voice/session", {method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error", signal: controller.signal, headers: {Authorization: `Bearer ${token}`, "Content-Type": "application/json"}, body: JSON.stringify({id: crypto.randomUUID()})});
      if (!response.ok) {
        const error = new Error("Voice session unavailable");
        error.voiceMessage = ({401: "Unlock operator controls before using the microphone.", 409: "That speech session was already requested. Click the microphone to start a new one.", 429: "Please wait a few seconds before starting another recording.", 503: "Voice is not configured yet. You can still use the text field.", 504: "The speech provider timed out. No automatic retry was made."})[response.status] || "The speech service could not start. Use text or try the microphone again.";
        throw error;
      }
      const result = await response.json();
      if (signal.aborted || operatorToken !== token) throw new Error("Cancelled");
      return result;
    } finally {clearTimeout(timer); signal.removeEventListener("abort", abort);}
  }
  function toggleVoice() {
    if (voiceSession?.active) {voiceSession.stop(); return;}
    if (!voiceAvailable()) return;
    if (!globalThis.HoloVoice?.Session) {voiceMessage = "Voice streaming is unavailable. Use the text field instead."; renderVoice(); return;}
    if (!voiceSession) voiceSession = new globalThis.HoloVoice.Session({
      requestSession: createVoiceSession,
      onState(phase) {
        const previous = voicePhase;
        voicePhase = phase;
        if (phase === "listening") {voiceStartedAt = Date.now(); voiceMessage = "Speak naturally. Final transcripts are sent to the agent.";}
        if (["idle", "error", "finishing"].includes(phase)) voiceStartedAt = null;
        if (phase === "idle" && previous === "finishing" && !voiceQueue.length && operatorCommand?.status !== "pending") voiceMessage = "Microphone off. Start another recording or type below.";
        if (phase === "finishing") voiceMessage = "Microphone off. Waiting briefly for the final transcript…";
        renderVoice();
      },
      onPartial(value) {voicePartial = value; renderVoice();},
      onFinal(value) {queueVoice({...value, run_id: voiceRunId});},
      onError(message) {voiceMessage = message; renderVoice();}
    });
    voiceRunId = envelope?.state?.run_id || null;
    voicePartial = ""; voiceMessage = "Requesting microphone access…";
    voiceSession.start();
  }
  function queueVoice(value) {
    const textValue = typeof value.text === "string" ? value.text.trim() : "";
    if (!voiceAvailable() || value.run_id !== (envelope?.state?.run_id || null)) {voiceMessage = "The run or operator connection changed. This instruction was not sent."; renderVoice(); return;}
    if (!textValue || textValue.length > 1000) {voiceMessage = "Use an instruction under 1,000 characters."; renderVoice(); return;}
    if (voiceQueue.length >= 5) {voiceMessage = "Five instructions are already waiting. Let the agent catch up before sending another."; renderVoice(); return;}
    const item = {...value, text: textValue, id: crypto.randomUUID(), queued_at: Date.now()};
    voiceQueue.push(item);
    voiceDisplayRunId = value.run_id;
    voiceCurrentCommandId = item.id;
    voiceSeenReceivedSeq = Math.max(voiceSeenReceivedSeq, n(latestEvent(envelope?.state?.events || [], "voice_received")?.seq));
    voiceTranscript = item.text; voiceReply = "";
    voiceMessage = "Instruction queued for the agent.";
    renderVoice();
  }
  function sendTypedVoice(event) {
    event?.preventDefault();
    const value = $("voice-text").value;
    if (!voiceAvailable() || !value.trim()) return;
    $("voice-text").value = "";
    queueVoice({text: value, run_id: envelope?.state?.run_id || null, session_id: "typed", utterance_id: crypto.randomUUID()});
  }
  async function sendNextVoice() {
    if (!voiceAvailable() || !voiceQueue.length || commandBusy || commandChecking || operatorCommand?.status === "pending" || Date.now() < voiceRetryAt) return;
    const item = voiceQueue.shift();
    if (item.run_id !== (envelope?.state?.run_id || null) || Date.now() - item.queued_at > 30000) {voiceMessage = "An unsent instruction expired or belonged to a previous run. Please say it again if needed."; renderVoice(); return;}
    const token = operatorToken;
    const {id, text: transcript, run_id, session_id, utterance_id} = item;
    operatorCommand = {id, action: "voice", run_id, status: "pending", submitted_at: Date.now()};
    try {storage?.setItem("holospeed.command", JSON.stringify({id, submitted_at: operatorCommand.submitted_at}));} catch { /* In-memory command tracking still prevents replay. */ }
    const submittedAt = operatorCommand.submitted_at;
    commandBusy = true;
    voiceMessage = "Sending instruction…"; renderControls();
    try {
      const result = await commandRequest("/api/commands", {id, action: "voice", text: transcript, run_id, session_id, utterance_id});
      if (operatorToken !== token || operatorCommand?.id !== id) return;
      operatorCommand = {...result, action: "voice", submitted_at: submittedAt};
      commandMessage = result.detail || "Voice instruction received.";
      handleVoiceCommand(operatorCommand);
      if (result.status === "pending") voiceMessage = "Instruction accepted. Waiting for the agent…";
    } catch (error) {
      if (operatorToken !== token || operatorCommand?.id !== id) return;
      if (error.status === 409) {
        operatorCommand = null;
        voiceQueue.unshift(item); voiceRetryAt = Date.now() + 1000;
        voiceMessage = "Another command is finishing. This instruction is waiting.";
      } else if (error.status === 401 || error.status === 503) lockControls("Operator access is unavailable. Unlock controls again when the agent is ready.");
      else if (error.status && error.status < 500) {operatorCommand = null; voiceMessage = "The instruction was not accepted. Please check the current run and try again.";}
      else {voiceMessage = "Delivery is unconfirmed. Checking this instruction ID without sending it again."; commandMessage = voiceMessage;}
    } finally {commandBusy = false; renderControls(); renderEngineering();}
  }
  function handleVoiceCommand(command) {
    if (command?.action !== "voice" || command.id !== voiceCurrentCommandId || command.status === "pending") return;
    if (command.status === "completed") {voiceReply = typeof command.data?.reply === "string" ? command.data.reply : command.detail || "Instruction received."; voiceMessage = "Agent replied.";}
    else voiceMessage = command.detail || `Instruction ${command.status}. No automatic resubmission.`;
  }
  function renderVoice() {
    const currentRun = envelope?.state?.run_id || null;
    if (voiceDisplayRunId !== currentRun) {
      voiceDisplayRunId = currentRun; voiceSeenReceivedSeq = -1;
      voiceTranscript = ""; voiceReply = ""; voiceCurrentCommandId = null;
    }
    if (voiceSession?.active && (!operatorToken || voiceRunId !== (envelope?.state?.run_id || null))) {
      voiceQueue.length = 0; voiceSession.cancel(); voiceMessage = "The run changed. Microphone off; start a new recording when ready.";
    }
    const active = voiceSession?.active === true;
    const available = voiceAvailable();
    const labels = {permission: "Allow microphone", connecting: "Connecting", listening: "Listening", finishing: "Finalizing", error: "Microphone off", idle: "Microphone off"};
    $("voice-panel").classList.toggle("listening", voicePhase === "listening");
    $("voice-toggle").disabled = active ? voicePhase === "finishing" : !available;
    $("voice-toggle").setAttribute("aria-pressed", String(active));
    text("voice-toggle-label", active ? voicePhase === "finishing" ? "Finishing…" : "Stop listening" : "Speak");
    text("voice-status", labels[voicePhase] || "Microphone off");
    text("voice-timer", voiceStartedAt == null ? "" : `${elapsed(Date.now() - voiceStartedAt)} / 02:00`);
    $("voice-timer").hidden = voiceStartedAt == null;
    text("voice-caption", voicePartial || "Live words appear here while you speak.");
    $("voice-caption").hidden = !voicePartial;
    $("voice-text").disabled = !available;
    $("voice-send").disabled = !available || voiceQueue.length >= 5;
    text("voice-feedback", voiceMessage || (!operatorToken ? "Unlock controls below to speak or send a message." : !available ? "Waiting for the agent connection. Text and voice will be available when it returns." : "Try “pause”, “collect more wood”, or “what are you doing?”"));
    const events = envelope?.state?.events || [];
    const received = latestEvent(events, "voice_received");
    if (received && n(received.seq) > voiceSeenReceivedSeq) {
      voiceSeenReceivedSeq = n(received.seq);
      const pendingLocal = voiceQueue.some((item) => item.id === voiceCurrentCommandId) || operatorCommand?.id === voiceCurrentCommandId && operatorCommand?.status === "pending";
      if (!pendingLocal || received.data?.command_id === voiceCurrentCommandId) {
        voiceCurrentCommandId = received.data?.command_id;
        voiceTranscript = typeof received.data?.text === "string" ? received.data.text : "";
        voiceReply = "";
      }
    }
    const response = latestEvent(events.filter((event) => event.data?.command_id === voiceCurrentCommandId), "voice_reply");
    if (response?.data?.command_id === voiceCurrentCommandId && typeof response.data.reply === "string") voiceReply = response.data.reply;
    $("voice-conversation").hidden = !voiceTranscript;
    text("voice-transcript", voiceTranscript);
    $("voice-reply-row").hidden = !voiceReply;
    text("voice-reply", voiceReply);
    if (available && voiceQueue.length && !commandBusy && !commandChecking && operatorCommand?.status !== "pending" && Date.now() >= voiceRetryAt) sendNextVoice();
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
    const portalSkill = ["portal", "cast_portal"].includes(state.action?.skill);
    text("skill-name", skill ? portalSkill ? "Navigate · build · enter Nether" : `${skill.name} · ${skill.item}` : "No active skill recorded");
    text("skill-count", skill ? portalSkill ? "Portal route" : `${skill.count == null ? "—" : count(skill.count)} / ${skill.target == null ? "—" : count(skill.target)}` : "—");
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
  function renderMission(state, telemetry) {
    if (inferenceRun !== state.run_id) { inferenceRun = state.run_id; inferenceEvidence = []; }
    const available = state.run_id ? [...inferenceEvidence, ...(Array.isArray(state.events) ? state.events : []), ...(selectedRun === state.run_id ? journal.events : [])] : [];
    const retained = [];
    for (const provider of ["holo", "jev"]) {
      const stage = inferenceStage(available, provider, state, telemetry.fresh);
      for (const event of [stage.latest, stage.result, stage.failed]) if (event) retained.push(event);
      const card = $(`mission-${provider}`);
      card.classList.toggle("active", stage.active);
      card.classList.toggle("recorded", stage.latest?.data?.phase === "complete");
      card.classList.toggle("failed", stage.latest?.data?.phase === "failed");
      text(`mission-${provider}-status`, stage.status);
      text(`mission-${provider}-task`, stage.task || (provider === "holo" ? "Chooses the next objective" : "Checks the next skill"));
      $(`mission-${provider}-task`).title = String(stage.latest?.data?.task || "");
      text(`mission-${provider}-rationale`, stage.rationale || "No decision recorded yet.");
      $(`mission-${provider}-rationale`).title = String(stage.result?.data?.rationale || "");
      text(`mission-${provider}-reason-label`, stage.result ? "LAST DECISION" : "AWAITING FIRST CALL");
      text(`mission-${provider}-input`, stage.inputSource === "screenshot_and_state" ? "Vision + game state" : stage.inputSource === "state" ? "Game state" : "No input recorded");
      $(`mission-${provider}-input`).dataset.source = stage.inputSource || "none";
      text(`mission-${provider}-error`, stage.latest?.data?.phase === "failed" ? concise(stage.latest.data.reason || "Request did not complete.") : "");
      $(`mission-${provider}-error`).hidden = stage.latest?.data?.phase !== "failed";
    }
    inferenceEvidence = mergeEvents([], retained);
    const holo = inferenceStage(inferenceEvidence, "holo", state, telemetry.fresh);
    const jev = inferenceStage(inferenceEvidence, "jev", state, telemetry.fresh);
    const skill = skillTelemetry(state.action, resourceEvidence);
    const skillStamp = timestamp(skill?.event?.timestamp);
    const skillAge = skillStamp == null ? null : Date.now() - skillStamp;
    const executing = Boolean(telemetry.fresh && state.status === "running" && skill?.event?.data?.skill?.active === true && skillAge != null && skillAge >= -2000 && skillAge < 5000);
    $("mission-executor").classList.toggle("active", executing);
    text("mission-executor-status", executing ? "Executing" : !telemetry.fresh && skill ? "Last recorded" : state.action?.skill && state.status === "running" ? "Awaiting progress" : "Standby");
    const phase = !telemetry.fresh ? "Telemetry unavailable" : holo.active ? "Holo4 is planning" : jev.active ? "Jev is checking" : executing ? "Acting in Minecraft" : state.status === "preparing" ? "Waiting for the fresh world" : state.status === "paused" ? "Agent paused" : state.status === "error" ? "Needs attention" : "Ready for the next step";
    text("mission-flow-state", phase);
    $("mission-flow-state").classList.toggle("active", holo.active || jev.active || executing);
    const progress = $("executor-progress");
    const quantified = executing && !["portal", "cast_portal"].includes(state.action?.skill) && Number.isFinite(skill?.count) && Number.isFinite(skill?.target) && skill.target > 0;
    progress.hidden = !executing;
    if (quantified) { progress.max = skill.target; progress.value = Math.min(skill.target, Math.max(0, skill.count)); }
    else progress.removeAttribute?.("value");
    progress.setAttribute("aria-label", quantified ? `${skill.count} of ${skill.target} items confirmed` : "Local skill is executing; completion is not quantified");
  }
  function engineeringCanPrepare() {
    const age = timestamp(envelope?.updated_at);
    const publisherFresh = reachable && age != null && Date.now() - age >= -2000 && Date.now() - age < FRESH_FRAME_MS;
    return Boolean(operatorToken && publisherFresh && engineeringRun && !commandBusy && operatorCommand?.status !== "pending");
  }
  function handleEngineeringCommand(command) {
    if (!operatorToken) return;
    if (!["prepare_investigation", "investigate"].includes(command?.action) || command.status === "pending") return;
    const identity = `${command.id}:${command.status}`;
    if (handledEngineeringCommand === identity) return;
    handledEngineeringCommand = identity;
    if (command.status !== "completed") {
      engineeringMessage = command.detail || `Investigation command ${command.status}. No request will be retried automatically.`;
      return;
    }
    if (command.action === "prepare_investigation") {
      const preview = investigationPreview(command.data, engineeringRun);
      if (!preview) { engineeringMessage = "The prepared brief could not be verified. Prepare it again before submitting."; engineeringPreview = null; return; }
      engineeringPreview = preview;
      $("devin-confirm").checked = false;
      if (preview.existing) engineeringByRun.set(engineeringRun, preview.existing);
      engineeringMessage = preview.can_submit ? "Review the exact brief below, then confirm one investigation." : preview.existing ? "This run already has a recorded investigation. Open its session or review the findings." : "The brief is ready, but submission is unavailable. Check the local Devin API configuration.";
    } else {
      if (command.data?.run_id) engineeringByRun.set(command.data.run_id, command.data);
      engineeringPreview = null;
      $("devin-confirm").checked = false;
      engineeringMessage = command.detail || "Investigation submitted. Status and findings will appear here.";
      engineeringLastFetched = 0;
    }
  }
  function selectEngineeringRun(runId) {
    if (engineeringRun === runId) return;
    engineeringRun = runId || null;
    engineeringPreview = null; engineeringMessage = "";
    engineeringLastFetched = 0; engineeringBusy = false; engineeringSelectionVersion++;
    $("devin-confirm").checked = false;
    renderEngineering();
    if (engineeringRun) fetchEngineering();
  }
  function openEngineering() {
    const next = selectedRun || envelope?.state?.run_id || envelope?.runs?.[0]?.id || null;
    if (operatorCommand?.status !== "pending" || !["prepare_investigation", "investigate"].includes(operatorCommand.action)) selectEngineeringRun(next);
    const dialog = $("devin-dialog");
    if (!dialog.open) {
      if (dialog.showModal) dialog.showModal();
      else { dialog.open = true; dialog.setAttribute("open", ""); }
    }
    renderEngineering();
    if (engineeringRun && !engineeringBusy) fetchEngineering();
  }
  function closeEngineering() {
    const dialog = $("devin-dialog");
    if (dialog.close) dialog.close();
    else { dialog.open = false; dialog.removeAttribute?.("open"); }
  }
  async function fetchEngineering() {
    if (!engineeringRun || engineeringBusy) return;
    engineeringBusy = true;
    const runId = engineeringRun, version = engineeringSelectionVersion;
    try {
      const result = await get(`/api/runs/${encodeURIComponent(runId)}`);
      if (version !== engineeringSelectionVersion || runId !== engineeringRun) return;
      if (result.engineering) engineeringByRun.set(runId, result.engineering);
      engineeringLastFetched = Date.now();
      renderEngineering();
    } catch (error) {
      if (version !== engineeringSelectionVersion || runId !== engineeringRun) return;
      engineeringLastFetched = Date.now();
      if (!engineeringByRun.has(runId)) engineeringMessage = error.name === "AbortError" ? "The investigation status request timed out." : "Investigation status is not available yet.";
      renderEngineering();
    } finally { if (version === engineeringSelectionVersion) engineeringBusy = false; }
  }
  function renderEngineering() {
    const runs = envelope?.runs || [];
    const select = $("devin-run");
    const signature = runs.map((run) => run.id).join("|");
    if (select.dataset.runs !== signature) {
      select.dataset.runs = signature;
      const options = runs.map((run) => {const option = document.createElement("option"); option.value = run.id; option.textContent = `${run.id}${run.metrics?.errors ? ` · ${run.metrics.errors} errors` : ""}`; return option;});
      if (!options.length) { const option = document.createElement("option"); option.value = ""; option.textContent = "No saved runs yet"; options.push(option); }
      select.replaceChildren(...options);
    }
    if (engineeringRun) select.value = engineeringRun;
    const pending = commandBusy || operatorCommand?.status === "pending";
    select.disabled = pending;
    const value = engineeringByRun.get(engineeringRun) || null;
    const preview = operatorToken && engineeringPreview?.run_id === engineeringRun ? engineeringPreview : null;
    const canPrepare = engineeringCanPrepare();
    $("devin-prepare").disabled = !canPrepare;
    $("devin-submit").disabled = !canPrepare || !preview?.can_submit || Boolean(value) || !$("devin-confirm").checked;
    $("devin-review").hidden = !preview;
    $("devin-confirmation").hidden = !preview?.can_submit;
    text("devin-prompt", preview?.prompt || "");
    text("devin-digest", preview ? `SHA-256 ${preview.digest_hash}` : "");
    text("devin-feedback", engineeringMessage || (!operatorToken ? "Unlock controls at the bottom of the page to prepare an investigation. Existing findings remain viewable." : engineeringRun ? "Prepare a redacted brief from this run, review it, then send it to Devin." : "Choose a recorded run to investigate."));
    text("devin-session-status", engineeringStatus(value));
    text("devin-session-detail", value?.status_detail ? `Reported state: ${value.status_detail}${value.updated_at ? ` · updated ${new Date(value.updated_at).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}` : ""}` : value?.updated_at ? `Updated ${new Date(value.updated_at).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}` : "No session has been recorded for this run.");
    const sessionUrl = devinSessionUrl(value?.url);
    $("devin-session-link").hidden = !sessionUrl;
    $("devin-session-link").href = sessionUrl || "#";
    text("devin-session-error", value?.error || "");
    $("devin-session-error").hidden = !value?.error;
    const current = engineeringByRun.get(selectedRun || envelope?.state?.run_id);
    text("devin-launch-status", current ? engineeringStatus(current) : "Find failures. Build better runs.");
    const output = value?.structured_output || {};
    const findingsSignature = JSON.stringify([engineeringRun, output]);
    if (findingsSignature !== engineeringFindingsSignature) {
    engineeringFindingsSignature = findingsSignature;
    text("devin-findings-summary", typeof output.summary === "string" ? output.summary : "Devin’s findings will appear when the session reports them.");
    for (const [key, id] of [["likely_causes", "devin-causes"], ["proposed_tests", "devin-tests"], ["proposed_fixes", "devin-fixes"], ["missing_evidence", "devin-missing"]]) {
      const entries = Array.isArray(output[key]) ? output[key].filter((item) => typeof item === "string").slice(0, 12) : [];
      const nodes = entries.map((item) => {const li = document.createElement("li"); li.textContent = item; return li;});
      if (!nodes.length) { const li = document.createElement("li"); li.className = "finding-empty"; li.textContent = "Not reported yet"; nodes.push(li); }
      $(id).replaceChildren(...nodes);
    }
    const patches = Array.isArray(output.test_patches) ? output.test_patches.slice(0, 8) : [];
    const patchNodes = patches.filter((patch) => patch && typeof patch.relative_path === "string" && typeof patch.code === "string").map((patch) => {
      const details = document.createElement("details"); details.className = "devin-patch";
      const summary = document.createElement("summary"); summary.textContent = patch.relative_path;
      const note = document.createElement("p"); note.textContent = `${patch.purpose || "Proposed test"} · ${patch.validation_status || "Not validated by this dashboard"}`;
      const code = document.createElement("pre"); code.textContent = patch.code;
      details.append(summary, note, code); return details;
    });
    $("devin-patches").replaceChildren(...patchNodes);
    $("devin-patches-section").hidden = !patchNodes.length;
    }
    if ($("devin-dialog").open && engineeringRun && !engineeringBusy && Date.now() - engineeringLastFetched > 10000) fetchEngineering();
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
      if (result.engineering) { engineeringByRun.set(runId, result.engineering); renderEngineering(); }
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

  $("voice-toggle").addEventListener("click", toggleVoice);
  $("voice-form").addEventListener("submit", sendTypedVoice);
  globalThis.addEventListener?.("pagehide", () => {voiceQueue.length = 0; voiceSession?.cancel();});
  $("operator-login-form").addEventListener("submit", unlockControls);
  $("operator-footer-lock").addEventListener("click", () => lockControls());
  $("devin-open").addEventListener("click", openEngineering);
  $("devin-open-journal").addEventListener("click", openEngineering);
  $("devin-close").addEventListener("click", closeEngineering);
  $("devin-run").addEventListener("change", () => selectEngineeringRun($("devin-run").value));
  $("devin-confirm").addEventListener("change", renderEngineering);
  $("devin-prepare").addEventListener("click", () => {
    engineeringPreview = null; $("devin-confirm").checked = false;
    submitCommand("prepare_investigation", {run_id: engineeringRun});
  });
  $("devin-submit").addEventListener("click", () => {
    if (engineeringPreview) submitCommand("investigate", {run_id: engineeringRun, digest_hash: engineeringPreview.digest_hash});
  });
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
