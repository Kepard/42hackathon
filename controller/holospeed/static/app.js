"use strict";

const $ = (id) => document.getElementById(id);
const milestoneNames = {wood: "Gather wood", crafting_table: "Craft a table", wooden_pickaxe: "Make a pickaxe", stone_tools: "Reach stone tools"};
let liveState = null;
let displayedState = null;
let stateReceivedAt = performance.now();
let socket = null;
let reconnectTimer = null;
let toastTimer = null;
let runList = [];
const comparisonSelection = new Set();
let replay = null;
let replayPosition = 0;
let replayPlaying = false;
let previousTick = performance.now();
let requestBusy = false;
let selectedRunId = null;

function text(id, value) { $(id).textContent = value; }
function number(value) { return Number.isFinite(Number(value)) ? Number(value) : 0; }
function time(ms, fractions = false) {
  const seconds = Math.max(0, number(ms)) / 1000;
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  const base = `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  return fractions ? `${base}.${Math.floor((seconds % 1) * 10)}` : base;
}
function setTimer(ms) {
  const [base, fraction] = time(ms, true).split(".");
  const span = document.createElement("span");
  span.textContent = `.${fraction}`;
  $("timer").replaceChildren(document.createTextNode(base), span);
}
function toast(message, error = false) {
  clearTimeout(toastTimer);
  text("toast", message);
  $("toast").classList.toggle("error", error);
  $("toast").hidden = false;
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, error ? 8000 : 4000);
}
async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${response.status})`);
  return data;
}
function latency(id, value) {
  const label = document.createElement("small");
  label.textContent = "ms";
  const measured = value !== null && value !== undefined && Number.isFinite(Number(value));
  $(id).replaceChildren(document.createTextNode(measured ? Math.round(Number(value)).toLocaleString() : "—"), label);
}
function sparkline(id, history) {
  const values = (history || []).slice(-36).map((value) => typeof value === "object" ? number(value.latency_ms ?? value.value) : number(value));
  const path = $(id).querySelector(".spark-data");
  if (values.length < 2) { path.setAttribute("d", ""); return; }
  const max = Math.max(...values, 1) * 1.1;
  const points = values.map((value, index) => `${index === 0 ? "M" : "L"}${(index / (values.length - 1) * 280).toFixed(2)},${(30 - value / max * 26).toFixed(2)}`);
  path.setAttribute("d", points.join(" "));
}
function renderInputs(state) {
  const action = state.action || {};
  const held = new Set(state.keys || action.keys || []);
  if (action.cursor?.button === "left") held.add("attack");
  if (action.cursor?.button === "right") held.add("use");
  document.querySelectorAll("[data-key]").forEach((element) => element.classList.toggle("active", held.has(element.dataset.key)));
  text("input-duration", held.size || action.yaw_delta || action.pitch_delta || action.cursor ? `${number(action.duration_ticks)} TICKS` : "IDLE");
  text("camera-delta", `Yaw ${number(action.yaw_delta).toFixed(1)}° / Pitch ${number(action.pitch_delta).toFixed(1)}°`);
  const extra = (state.keys || []).filter((key) => ["inventory", "drop", "swap", "escape"].includes(key));
  if (action.hotbar) extra.push(`slot ${action.hotbar}`);
  if (action.scroll) extra.push(`scroll ${action.scroll}`);
  text("extra-inputs", extra.length ? extra.join(" · ") : held.size ? `${held.size} active input${held.size === 1 ? "" : "s"}` : "No held inputs");
  text("mouse-label", action.cursor ? `X ${Math.round(action.cursor.x * 100)} Y ${Math.round(action.cursor.y * 100)}` : "MOUSE");
}
function eventDescription(event) {
  const data = event.data || {};
  if (event.type === "decision") return `${String(data.provider || "agent").toUpperCase()} · ${data.decision?.objective || data.objective || "Decision recorded"}${data.latency_ms != null ? ` · ${Math.round(data.latency_ms)} ms` : ""}`;
  if (event.type === "plan") return `${String(data.provider || "holo").toUpperCase()} · Plan: ${data.decision?.objective || data.objective || "Visual plan recorded"} · awaiting fresh Jev validation`;
  if (event.type === "inference_metrics") {
    const usage = data.usage || {};
    const input = usage.input_tokens ?? usage.prompt_tokens;
    const output = usage.output_tokens ?? usage.completion_tokens;
    const tokens = input == null && output == null ? "usage unavailable" : `${input == null ? "—" : number(input).toLocaleString()} in / ${output == null ? "—" : number(output).toLocaleString()} out`;
    return `${String(data.provider || "model").toUpperCase()} · ${data.latency_ms == null ? "latency unavailable" : `${Math.round(number(data.latency_ms)).toLocaleString()} ms`} · ${tokens}`;
  }
  if (event.type === "action") {
    const action = data.action || data;
    const parts = [...(action.keys || [])];
    if (action.yaw_delta) parts.push(`yaw ${action.yaw_delta}°`);
    if (action.pitch_delta) parts.push(`pitch ${action.pitch_delta}°`);
    if (action.cursor) parts.push(`${action.cursor.button} click`);
    if (action.hotbar) parts.push(`slot ${action.hotbar}`);
    return `${parts.join(" + ") || "Observe / wait"} · ${number(action.duration_ticks)} ticks`;
  }
  if (event.type === "observation") return `Frame ${data.frame_id ? String(data.frame_id).slice(0, 12) : "captured"}${data.width ? ` · ${data.width} × ${data.height}` : ""}`;
  if (event.type === "milestone") return `${milestoneNames[data.id || data.milestone] || data.id || data.milestone || "Milestone"}${data.evidence ? ` · ${data.evidence}` : ""}`;
  if (event.type === "ack") return `Input ${data.status || "acknowledged"}${data.reason ? ` · ${data.reason}` : ""}`;
  return String(data.message || data.detail || data.reason || data.error || data.name || data.status || (Object.keys(data).length ? JSON.stringify(data) : event.type));
}
function renderEvents(events) {
  const visible = [...(events || [])].reverse().slice(0, 80);
  text("event-count", (events || []).length);
  const fragment = document.createDocumentFragment();
  if (!visible.length) {
    const empty = document.createElement("p");
    empty.className = "list-empty";
    empty.textContent = "Decisions, inputs, and observations will appear here.";
    fragment.append(empty);
  }
  for (const event of visible) {
    const row = document.createElement("div");
    row.className = "event-row";
    const stamp = document.createElement("span");
    stamp.className = "event-time";
    stamp.textContent = time(event.elapsed_ms);
    const kind = document.createElement("span");
    kind.className = "event-kind";
    if (["decision", "error", "milestone"].includes(event.type)) kind.classList.add(event.type);
    if (event.type === "plan") kind.classList.add("decision");
    kind.textContent = event.type === "inference_metrics" ? "METRICS" : String(event.type || "event").toUpperCase();
    kind.title = event.type || "event";
    const message = document.createElement("span");
    message.className = "event-message";
    message.textContent = eventDescription(event);
    row.append(stamp, kind, message);
    fragment.append(row);
  }
  $("event-list").replaceChildren(fragment);
}
function frameURL(value) {
  if (!value) return null;
  try {
    const url = new URL(value, location.origin);
    return url.origin === location.origin && url.pathname.startsWith("/api/runs/") ? url.href : null;
  } catch { return null; }
}
function render(state) {
  displayedState = state;
  const isReplay = Boolean(replay);
  const isDemo = state.mode !== "live";
  const active = state.status === "running";
  text("status-label", isReplay ? "REPLAY" : ({idle: "STANDBY", running: "RUNNING", paused: "PAUSED", error: "ATTENTION", completed: "COMPLETE", finished: "COMPLETE"}[state.status] || String(state.status || "STANDBY").toUpperCase()));
  $("run-dot").className = `status-dot ${active && !isReplay ? "running" : state.status === "error" ? "error" : ""}`;
  text("mode-badge", isReplay ? `${isDemo ? "DEMO" : "LIVE"} · REPLAY` : isDemo ? "DEMO · SIMULATION" : "LIVE · MINECRAFT");
  $("mode-badge").className = `mode-badge ${isReplay ? "replay" : !isDemo ? "live" : ""}`;
  text("footer-mode", isReplay ? "RECORDED EVIDENCE · REPLAY" : isDemo ? "SIMULATION · NO API CALLS" : "LIVE MODE · VISUAL DECISIONS");
  text("latency-note", isDemo ? "Simulated latency. Demo mode makes no model API calls." : "Measured round-trip latency, including inference.");
  text("objective", state.objective || "Make the first move.");
  text("strategy", state.strategy || "The agent’s visual plan will appear when a run begins.");
  text("expected-effect", state.expected_effect || "Waiting for a decision");
  text("run-id", state.run_id || "Awaiting first run");
  $("run-id").title = state.run_id || "";
  const metrics = state.metrics || {};
  for (const key of ["actions", "observations", "recoveries"]) text(`metric-${key}`, state.run_id ? number(metrics[key]).toLocaleString() : "—");
  const tokens = number(metrics.input_tokens) + number(metrics.output_tokens);
  text("metric-tokens", state.run_id ? tokens.toLocaleString() : "—");
  text("token-split", state.run_id ? `${number(metrics.input_tokens).toLocaleString()} in / ${number(metrics.output_tokens).toLocaleString()} out` : "Input + output");
  text("error-count", `${number(metrics.errors)} error${number(metrics.errors) === 1 ? "" : "s"}`);
  text("frame-counter", state.frame_url ? `FRAME ${String(number(metrics.observations)).padStart(4, "0")}` : "NO FRAME");
  const url = frameURL(state.frame_url);
  const image = $("game-frame");
  if (url) {
    if (image.src !== url) image.src = url;
    image.hidden = false;
    $("screen-empty").hidden = true;
  } else {
    image.hidden = true;
    $("screen-empty").hidden = false;
    text("empty-description", isReplay ? "No observation has been recorded at this point in the run." : isDemo ? "Start a simulated run to explore the control loop." : state.connected ? "Minecraft is connected. Start a run to capture the first observation." : "Connect the Fabric bridge, then start your expedition.");
  }
  text("screen-tag", isDemo ? "SIMULATED OBSERVATION · DEMO" : "VISUAL-ONLY OBSERVATIONS");
  setTimer(state.elapsed_ms);
  renderInputs(state);
  latency("holo-latency", state.holo_latency_ms);
  latency("jev-latency", state.jev_latency_ms);
  sparkline("holo-spark", state.latency_history?.holo);
  sparkline("jev-spark", state.latency_history?.jev);
  renderEvents(state.events);
  const done = new Set((state.milestones || []).map((item) => typeof item === "string" ? item : item.id || item.milestone));
  let nextFound = false;
  document.querySelectorAll("[data-milestone]").forEach((item) => {
    const complete = done.has(item.dataset.milestone);
    item.classList.toggle("complete", complete);
    item.classList.toggle("active", Boolean(state.run_id) && !complete && !nextFound);
    if (!complete) nextFound = true;
    const evidence = (state.milestones || []).find((entry) => entry.id === item.dataset.milestone);
    item.title = evidence?.evidence || "";
  });
  $("pause-button").disabled = requestBusy || !active || isReplay;
  $("finish-button").disabled = requestBusy || !state.run_id || ["finished", "completed", "idle"].includes(state.status) || isReplay;
  $("start-button").disabled = requestBusy || active || (!isDemo && !state.connected) || isReplay;
  $("start-button").textContent = state.status === "paused" ? "Resume run" : "Start run";
}
function updateConnection(online) {
  $("connection").className = `connection ${online ? "online" : "offline"}`;
  text("connection-label", online ? liveState?.mode === "live" ? liveState.connected ? "Fabric bridge connected" : "Waiting for Fabric bridge" : "Controller connected" : "Reconnecting to controller");
}
function acceptState(state) {
  if (!state || typeof state !== "object" || !state.status) return;
  const oldRun = liveState?.run_id;
  liveState = state;
  stateReceivedAt = performance.now();
  updateConnection(true);
  if (!replay) render(state);
  if (oldRun !== state.run_id) loadRuns().catch(() => {});
}
async function connect() {
  try { acceptState(await api("/api/state")); } catch (error) { updateConnection(false); }
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${scheme}//${location.host}/events`);
  socket.addEventListener("open", () => updateConnection(true));
  socket.addEventListener("message", (event) => {
    try { const data = JSON.parse(event.data); acceptState(data.state || data); } catch { /* A malformed update does not replace the last valid state. */ }
  });
  socket.addEventListener("close", () => {
    updateConnection(false);
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 2000);
  });
  socket.addEventListener("error", () => socket.close());
}
async function command(name) {
  if (requestBusy || replay) return;
  requestBusy = true;
  if (liveState) render(liveState);
  try {
    await api(`/api/${name}`, {method: "POST"});
    acceptState(await api("/api/state"));
    if (name === "finish") await loadRuns();
  } catch (error) { toast(error.message, true); }
  finally { requestBusy = false; if (liveState && !replay) render(liveState); }
}
async function loadRuns() {
  const data = await api("/api/runs");
  runList = data.runs || [];
  const available = new Set(runList.map((run) => run.id));
  for (const id of comparisonSelection) if (!available.has(id)) comparisonSelection.delete(id);
  text("run-count", runList.length);
  const fragment = document.createDocumentFragment();
  if (!runList.length) {
    const empty = document.createElement("p");
    empty.className = "list-empty";
    empty.textContent = "Your first run starts the archive. Frames, decisions, and inputs are preserved together.";
    fragment.append(empty);
  }
  for (const run of runList) {
    const row = document.createElement("div");
    row.className = "archive-row";
    const selectionLabel = document.createElement("label");
    selectionLabel.className = "compare-toggle";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.dataset.compareRun = run.id;
    checkbox.checked = comparisonSelection.has(run.id);
    checkbox.setAttribute("aria-label", `Select ${run.id} for comparison`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked && comparisonSelection.size < 2) comparisonSelection.add(run.id);
      else comparisonSelection.delete(run.id);
      updateComparisonSelection();
    });
    selectionLabel.append(checkbox);
    const button = document.createElement("button");
    button.className = "archive-item";
    button.type = "button";
    const identity = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = run.id;
    const date = document.createElement("small");
    const stamp = new Date(run.started_at);
    date.textContent = Number.isNaN(stamp.getTime()) ? "Run recorded" : stamp.toLocaleString();
    identity.append(title, date);
    const mode = document.createElement("span");
    mode.textContent = run.mode === "live" ? "LIVE" : "DEMO";
    const duration = document.createElement("span");
    duration.textContent = time(run.duration_ms);
    const status = document.createElement("span");
    status.textContent = run.status || "recorded";
    const link = document.createElement("span");
    link.className = "replay-link";
    link.textContent = "Replay →";
    button.append(identity, mode, duration, status, link);
    button.addEventListener("click", () => openReplay(run.id));
    row.append(selectionLabel, button);
    fragment.append(row);
  }
  $("archive-list").replaceChildren(fragment);
  updateComparisonSelection();
}
function updateComparisonSelection() {
  document.querySelectorAll("[data-compare-run]").forEach((checkbox) => {
    const selected = comparisonSelection.has(checkbox.dataset.compareRun);
    checkbox.checked = selected;
    checkbox.disabled = !selected && comparisonSelection.size >= 2;
    checkbox.closest(".archive-row").classList.toggle("selected", selected);
  });
  $("compare-runs").disabled = comparisonSelection.size !== 2;
  $("clear-comparison").disabled = comparisonSelection.size === 0;
  text("comparison-selection", comparisonSelection.size ? `${comparisonSelection.size} of 2 runs selected` : "Select two runs to compare their recorded results.");
  if (comparisonSelection.size !== 2) $("comparison").hidden = true;
  else if (!$("comparison").hidden) renderComparison();
}
function renderComparison() {
  const runs = [...comparisonSelection].map((id) => runList.find((run) => run.id === id)).filter(Boolean);
  if (runs.length !== 2) return;
  const header = document.createElement("tr");
  const metricHead = document.createElement("th");
  metricHead.scope = "col";
  metricHead.textContent = "RECORDED METRIC";
  header.append(metricHead);
  for (const [index, run] of runs.entries()) {
    const cell = document.createElement("th");
    cell.scope = "col";
    const label = document.createElement("strong");
    label.textContent = `RUN ${index === 0 ? "A" : "B"}`;
    const id = document.createElement("span");
    id.className = "comparison-id";
    id.textContent = run.id;
    const mode = document.createElement("span");
    mode.className = `mode-badge${run.mode === "live" ? " live" : ""}`;
    mode.textContent = run.mode === "live" ? "LIVE · MINECRAFT" : "DEMO · SIMULATION";
    cell.append(label, id, mode);
    header.append(cell);
  }
  $("comparison-head").replaceChildren(header);
  const body = document.createDocumentFragment();
  function addRow(label, values, section = false) {
    const row = document.createElement("tr");
    if (section) row.className = "comparison-section";
    const heading = document.createElement("th");
    heading.scope = "row";
    heading.textContent = label;
    row.append(heading);
    for (const value of values) {
      const cell = document.createElement("td");
      if (value && typeof value === "object") {
        cell.textContent = value.text;
        if (value.evidence) cell.title = value.evidence;
      } else cell.textContent = String(value ?? "—");
      row.append(cell);
    }
    body.append(row);
  }
  const metric = (run, key) => run.metrics?.[key] == null ? "—" : number(run.metrics[key]).toLocaleString();
  addRow("Status", runs.map((run) => run.status || "—"));
  addRow("Elapsed duration", runs.map((run) => run.duration_ms == null ? "—" : time(run.duration_ms, true)));
  addRow("Actions executed", runs.map((run) => metric(run, "actions")));
  addRow("Observations preserved", runs.map((run) => metric(run, "observations")));
  addRow("Errors", runs.map((run) => metric(run, "errors")));
  addRow("Recoveries", runs.map((run) => metric(run, "recoveries")));
  addRow("Input tokens", runs.map((run) => metric(run, "input_tokens")), true);
  addRow("Output tokens", runs.map((run) => metric(run, "output_tokens")));
  addRow("Total tokens", runs.map((run) => run.metrics?.input_tokens == null && run.metrics?.output_tokens == null ? "—" : (number(run.metrics?.input_tokens) + number(run.metrics?.output_tokens)).toLocaleString()));
  for (const [index, [id, label]] of Object.entries(milestoneNames).entries()) {
    addRow(label, runs.map((run) => {
      const milestone = (run.milestones || []).find((entry) => entry.id === id || entry.milestone === id);
      return milestone ? {text: `${time(milestone.elapsed_ms, true)} ${run.mode === "live" ? "· visual claim" : "· simulated"}`, evidence: milestone.evidence} : "Not recorded";
    }), index === 0);
  }
  $("comparison-body").replaceChildren(body);
  $("comparison").hidden = false;
}
function showArchive() {
  $("archive").hidden = false;
  $("history-nav").classList.add("selected");
  $("live-nav").classList.remove("selected");
  text("view-label", "Run archive");
  loadRuns().catch((error) => toast(error.message, true));
  $("archive").scrollIntoView({behavior: "smooth", block: "start"});
}
function returnLive() {
  replayPlaying = false;
  replay = null;
  selectedRunId = null;
  $("replay-bar").hidden = true;
  $("return-live").hidden = true;
  $("live-controls").hidden = false;
  $("archive").hidden = true;
  $("live-nav").classList.add("selected");
  $("history-nav").classList.remove("selected");
  text("view-label", "Run control");
  text("page-title", "Run control.");
  text("page-description", "A new world. A clear objective. Every decision, visible.");
  if (liveState) render(liveState);
}
async function openReplay(id) {
  selectedRunId = id;
  try {
    const data = await api(`/api/runs/${encodeURIComponent(id)}`);
    if (selectedRunId !== id) return;
    replay = {summary: data.summary || {}, events: (data.events || []).sort((a, b) => number(a.elapsed_ms) - number(b.elapsed_ms))};
    replayPosition = 0;
    replayPlaying = false;
    const end = replay.events.reduce((latest, event) => Math.max(latest, number(event.elapsed_ms)), Math.max(number(replay.summary.duration_ms), 1));
    replay.duration = end;
    $("replay-seek").max = end;
    $("replay-seek").value = 0;
    $("replay-bar").hidden = false;
    $("return-live").hidden = false;
    $("live-controls").hidden = true;
    $("archive").hidden = true;
    $("history-nav").classList.add("selected");
    $("live-nav").classList.remove("selected");
    text("view-label", "Run replay");
    text("page-title", "Run replay.");
    text("page-description", "Revisit the evidence. Find the moment that changed the run.");
    text("replay-title", id);
    text("replay-duration", time(end));
    $("download-events").href = `/api/runs/${encodeURIComponent(id)}/events`;
    renderReplay();
    $("replay-bar").scrollIntoView({behavior: "smooth", block: "nearest"});
  } catch (error) { toast(error.message, true); }
}
function replaySnapshot() {
  const summary = replay.summary;
  const events = replay.events.filter((event) => number(event.elapsed_ms) <= replayPosition);
  // New journals include every call, including rejected responses. Detect the
  // format across the whole journal so scrubbing cannot switch accounting modes.
  const hasInferenceMetrics = replay.events.some((event) => event.type === "inference_metrics");
  const state = {mode: summary.mode || "demo", status: "paused", run_id: summary.id || selectedRunId, elapsed_ms: replayPosition, connected: false, objective: "Reconstructing the run", strategy: "Scrub the timeline to inspect recorded decisions and their effects.", expected_effect: "Waiting for the first recorded decision", keys: [], action: null, holo_latency_ms: null, jev_latency_ms: null, metrics: {actions: 0, observations: 0, recoveries: 0, errors: 0, input_tokens: 0, output_tokens: 0}, milestones: [], events, frame_url: null, latency_history: {holo: [], jev: []}};
  for (const event of events) {
    const data = event.data || {};
    if (event.type === "inference_metrics" || (!hasInferenceMetrics && event.type === "decision")) {
      const provider = String(data.provider || "").toLowerCase().startsWith("holo") ? "holo" : String(data.provider || "").toLowerCase().includes("jev") ? "jev" : null;
      if (provider && data.latency_ms != null) {
        state[`${provider}_latency_ms`] = data.latency_ms;
        state.latency_history[provider].push(data.latency_ms);
      }
      state.metrics.input_tokens += number(data.usage?.input_tokens ?? data.usage?.prompt_tokens);
      state.metrics.output_tokens += number(data.usage?.output_tokens ?? data.usage?.completion_tokens);
    }
    if (event.type === "observation") {
      state.frame_url = data.frame_url || state.frame_url;
      state.metrics.observations++;
    } else if (event.type === "decision" || event.type === "plan") {
      const decision = data.decision || data;
      state.objective = decision.objective || state.objective;
      state.strategy = decision.strategy || state.strategy;
      if (event.type === "plan") {
        state.expected_effect = "Jev is validating this plan on a fresh screenshot";
        state.keys = [];
        state.action = null;
      } else {
        state.expected_effect = decision.expected_effect || state.expected_effect;
      }
    } else if (event.type === "action") {
      state.action = data.action || data;
      state.keys = state.action.keys || [];
    } else if (event.type === "ack") {
      if (data.status === "completed") state.metrics.actions++;
      state.keys = [];
      state.action = null;
    } else if (event.type === "milestone") {
      state.milestones.push({id: data.id || data.milestone, evidence: data.evidence || "", elapsed_ms: event.elapsed_ms});
    } else if (event.type === "error") {
      state.metrics.errors++;
      state.keys = [];
      state.action = null;
    } else if (event.type === "recovery" || data.name === "recovery") {
      state.metrics.recoveries++;
    }
    if (event.type === "run_finished" || (event.type === "event" && ["paused", "emergency_stop", "world_changed", "disconnected", "bridge_disconnected"].includes(data.name))) {
      state.keys = [];
      state.action = null;
    }
  }
  return state;
}
function renderReplay() {
  if (!replay) return;
  render(replaySnapshot());
  $("replay-seek").value = replayPosition;
  text("replay-time", time(replayPosition));
  text("replay-play", replayPlaying ? "Pause" : "Play");
  $("replay-play").setAttribute("aria-label", replayPlaying ? "Pause replay" : "Play replay");
}

$("start-button").addEventListener("click", () => command("start"));
$("pause-button").addEventListener("click", () => command("pause"));
$("finish-button").addEventListener("click", () => command("finish"));
$("live-nav").addEventListener("click", returnLive);
$("history-nav").addEventListener("click", showArchive);
$("return-live").addEventListener("click", returnLive);
$("refresh-runs").addEventListener("click", () => loadRuns().catch((error) => toast(error.message, true)));
$("compare-runs").addEventListener("click", () => { renderComparison(); $("comparison-title").focus({preventScroll: true}); $("comparison").scrollIntoView({behavior: "smooth", block: "nearest"}); });
$("clear-comparison").addEventListener("click", () => { comparisonSelection.clear(); updateComparisonSelection(); });
$("replay-seek").addEventListener("input", (event) => { replayPosition = Number(event.target.value); renderReplay(); });
$("replay-play").addEventListener("click", () => {
  if (!replay) return;
  if (replayPosition >= replay.duration) replayPosition = 0;
  replayPlaying = !replayPlaying;
  renderReplay();
});
$("game-frame").addEventListener("error", () => {
  $("game-frame").hidden = true;
  $("screen-empty").hidden = false;
  text("empty-description", "This recorded frame could not be loaded. Other run evidence remains available.");
});
document.addEventListener("visibilitychange", () => { if (document.hidden) replayPlaying = false; });
setInterval(() => {
  const now = performance.now();
  const delta = now - previousTick;
  previousTick = now;
  if (replay && replayPlaying) {
    replayPosition = Math.min(replay.duration, replayPosition + delta * Number($("replay-speed").value));
    if (replayPosition >= replay.duration) replayPlaying = false;
    renderReplay();
  } else if (!replay && displayedState?.status === "running") {
    setTimer(number(displayedState.elapsed_ms) + Math.min(now - stateReceivedAt, 5000));
  }
}, 100);
connect();
loadRuns().catch(() => {});
