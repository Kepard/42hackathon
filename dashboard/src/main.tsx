import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import DevinPanel from "./DevinPanel";
import "./style.css";

type Call = { name: string; arguments: Record<string, unknown> };
type Step = {
  step: number;
  intent: string[];
  tool_calls: Call[];
  executed_actions: Record<string, unknown>[];
  api_latency_ms: number;
  action_duration_ms: number;
  cost_usd: number | null;
  tokens: { in: number; cached: number; out: number; reasoning: number };
  errors: string[];
  failure_tags: string[];
  evaluator?: { completed: string[] };
  elapsed_seconds?: number;
};
type Summary = {
  success: boolean;
  score: number;
  steps: number;
  wall_time_seconds: number;
  mean_api_latency_ms: number;
  p95_api_latency_ms: number;
  total_cost_usd: number | null;
  milestones: Record<string, number>;
  config: { model: string; tier: string; timing: string };
  task: string;
  failure_tags: string[];
};
type Intervention = {
  timestamp: string;
  source: string;
  action: string;
  text?: string;
};
type Run = {
  id: string;
  status: string;
  summary: Summary | null;
  steps: Step[];
  interventions: Intervention[];
  metadata: {
    model?: string;
    tier?: string;
    task?: string;
    created_at?: string;
  };
};
type Board = {
  model: string;
  tier: string;
  task: string;
  runs: number;
  success_rate: number;
  mean_score: number;
  mean_latency_ms: number;
  p95_latency_ms: number;
  cost_per_run: number | null;
  time_to_milestone: Record<string, number>;
  failure_counts: Record<string, number>;
};
type Overlay = {
  objective?: string;
  intent?: string;
  badges?: string;
  current_rung?: string;
  completed?: number;
  api_latency_ms?: number;
};
const LADDER = [
  "log",
  "crafting_table",
  "wooden_pickaxe",
  "cobblestone",
  "stone_pickaxe",
  "furnace",
  "iron_ore",
  "iron_ingot",
  "kill_hostile_mob",
  "flint_and_steel",
  "obsidian",
  "portal",
  "enter_nether",
];
const title = (s: string) => s.replaceAll("_", " ");
const num = (n?: number, digits = 0) =>
  (n ?? 0).toLocaleString(undefined, { maximumFractionDigits: digits });
const usd = (n: number | null | undefined) =>
  n == null ? "Unknown" : `$${n.toFixed(4)}`;
async function api<T>(url: string, body?: unknown): Promise<T> {
  const response = await fetch(
    "/api" + url,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const data = await response.json();
  if (!response.ok)
    throw Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  return data;
}

function Frame({
  src,
  record,
  label,
  markers = false,
}: {
  src?: string;
  record?: Step;
  label: string;
  markers?: boolean;
}) {
  const points =
    record?.tool_calls.filter(
      (c) => c.name === "click_at" || c.name === "shift_click_at",
    ) ?? [];
  return (
    <div className="frame">
      <div className="frame-label">{label}</div>
      {src ? (
        <img src={src} alt={label} />
      ) : (
        <div className="empty">
          {label.includes("Model")
            ? "Waiting for the first model observation"
            : "Connecting to Minecraft…"}
        </div>
      )}
      {src &&
        markers &&
        (points.length ? (
          points.map((c, i) => (
            <span
              key={i}
              className="marker click"
              title={`${c.arguments.x}, ${c.arguments.y}`}
              style={{
                left: `${Number(c.arguments.x) / 10}%`,
                top: `${Number(c.arguments.y) / 10}%`,
              }}
            >
              {i + 1}
            </span>
          ))
        ) : (
          <span className="marker aim" title="World aim · image center">
            +
          </span>
        ))}
    </div>
  );
}
function Latency({ steps }: { steps: Step[] }) {
  const max = Math.max(1000, ...steps.map((s) => s.api_latency_ms));
  const pts = steps
    .map(
      (s, i) =>
        `${40 + (i * 540) / Math.max(1, steps.length - 1)},${115 - (s.api_latency_ms / max) * 90}`,
    )
    .join(" ");
  return (
    <section className="panel chart">
      <div className="section-head">
        <h3>API response time</h3>
        <span>per step · milliseconds</span>
      </div>
      <svg
        viewBox="0 0 600 145"
        role="img"
        aria-label="API latency per step chart"
      >
        {[0, 0.5, 1].map((v) => (
          <g key={v}>
            <line
              x1="40"
              x2="580"
              y1={115 - v * 90}
              y2={115 - v * 90}
              stroke="#253345"
            />
            <text x="0" y={119 - v * 90} fill="#8393a8" fontSize="10">
              {Math.round(v * max)}
            </text>
          </g>
        ))}
        <polyline points={pts} fill="none" stroke="#52ddba" strokeWidth="2.5" />
        {steps.map((s, i) => (
          <circle
            key={s.step}
            cx={40 + (i * 540) / Math.max(1, steps.length - 1)}
            cy={115 - (s.api_latency_ms / max) * 90}
            r="3"
            fill="#52ddba"
          >
            <title>
              Step {s.step + 1}: {num(s.api_latency_ms)} ms
            </title>
          </circle>
        ))}
        <text x="40" y="140" fill="#8393a8" fontSize="10">
          Step 1
        </text>
        <text x="530" y="140" fill="#8393a8" fontSize="10">
          Step {steps.length}
        </text>
      </svg>
    </section>
  );
}
function Timeline({ steps }: { steps: Step[] }) {
  return (
    <section className="panel">
      <div className="section-head">
        <h3>Decision timeline</h3>
        <span>{steps.length} recorded steps</span>
      </div>
      <div className="timeline">
        {steps.length === 0 && (
          <p className="muted">
            Every decision will appear here: declared intent, inputs, latency
            and cost.
          </p>
        )}
        {[...steps].reverse().map((s) => (
          <details className="step" key={s.step}>
            <summary>
              <span className="step-index">
                {String(s.step + 1).padStart(2, "0")}
              </span>
              <span className="step-intent">
                {s.intent.join(" / ") || "No tool call"}
                <small>
                  {s.tool_calls.map((c) => c.name).join(" → ") ||
                    s.errors.join(" · ")}
                </small>
              </span>
              <span className="step-time">{num(s.api_latency_ms)} ms</span>
              <span className="chevron">⌄</span>
            </summary>
            <div className="step-detail">
              <div className="tags">
                {s.failure_tags.map((t) => (
                  <span className="tag danger" key={t}>
                    {t}
                  </span>
                ))}
              </div>
              <p>
                Input {num(s.tokens.in)} · cached {num(s.tokens.cached)} ·
                output {num(s.tokens.out)} · reasoning {num(s.tokens.reasoning)}{" "}
                · {usd(s.cost_usd)} · action {num(s.action_duration_ms)} ms
              </p>
              <h4>Tool calls</h4>
              <pre>{JSON.stringify(s.tool_calls, null, 2)}</pre>
              <h4>Executed inputs</h4>
              <pre>{JSON.stringify(s.executed_actions, null, 2)}</pre>
              {s.errors.map((e, i) => (
                <p className="error" key={i}>
                  {e}
                </p>
              ))}
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}
function Metrics({
  steps,
  summary,
}: {
  steps: Step[];
  summary?: Summary | null;
}) {
  const average = steps.length
    ? steps.reduce((a, s) => a + s.api_latency_ms, 0) / steps.length
    : 0;
  const unknown = steps.some((s) => s.cost_usd == null);
  const cards = [
    ["Steps", num(steps.length), "decisions made"],
    [
      "API latency",
      `${num(summary?.mean_api_latency_ms ?? average)} ms`,
      "average response",
    ],
    [
      "Run cost",
      usd(
        summary
          ? summary.total_cost_usd
          : unknown
            ? null
            : steps.reduce((a, s) => a + (s.cost_usd ?? 0), 0),
      ),
      "input + output",
    ],
    [
      "Milestones",
      `${steps.at(-1)?.evaluator?.completed.length ?? Object.keys(summary?.milestones ?? {}).length} / 13`,
      "Survival → Nether",
    ],
  ];
  return (
    <div className="metrics">
      {cards.map(([label, value, sub]) => (
        <div className="metric" key={label}>
          <span>{label}</span>
          <strong>{value}</strong>
          <small>{sub}</small>
        </div>
      ))}
    </div>
  );
}
function Ladder({
  steps,
  summary,
}: {
  steps: Step[];
  summary?: Summary | null;
}) {
  const completed =
    steps.at(-1)?.evaluator?.completed ??
    Object.keys(summary?.milestones ?? {});
  return (
    <section className="panel">
      <div className="section-head">
        <h3>The Survival ladder</h3>
        <span>{completed.length}/13</span>
      </div>
      <div className="ladder">
        {LADDER.map((r, i) => {
          const time =
            summary?.milestones[r] ??
            steps.find((s) => s.evaluator?.completed.includes(r))
              ?.elapsed_seconds;
          return (
            <div
              className={`rung ${completed.includes(r) ? "done" : i === completed.length ? "next" : ""}`}
              key={r}
            >
              <span className="rung-dot">
                {completed.includes(r) ? "✓" : i + 1}
              </span>
              <span>{title(r)}</span>
              <small>
                {time == null
                  ? i === completed.length
                    ? "NEXT"
                    : "—"
                  : `${num(time, 1)}s`}
              </small>
            </div>
          );
        })}
      </div>
    </section>
  );
}
function App() {
  const [page, setPage] = useState("Live");
  const [connected, setConnected] = useState(false),
    [wsConnected, setWsConnected] = useState(false);
  const [active, setActive] = useState<string | null>(null),
    [phase, setPhase] = useState("IDLE");
  const [runActive, setRunActive] = useState(false);
  const [availableTasks, setAvailableTasks] = useState([
    "get_log",
    "craft_planks",
    "craft_table",
    "ladder",
  ]);
  const [overlay, setOverlay] = useState<Overlay>({});
  const [viewer, setViewer] = useState<string>(),
    [model, setModel] = useState<string>(),
    [modelStep, setModelStep] = useState(-1);
  const [steps, setSteps] = useState<Step[]>([]),
    [summary, setSummary] = useState<Summary | null>(null);
  const [interventions, setInterventions] = useState<Intervention[]>([]);
  const [runs, setRuns] = useState<Run[]>([]),
    [board, setBoard] = useState<Board[]>([]),
    [replay, setReplay] = useState<Run | null>(null),
    [replayIndex, setReplayIndex] = useState(0);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [goal, setGoal] = useState(""),
    [confirmation, setConfirmation] = useState<{
      confirmation_token: string;
      text: string;
    } | null>(null);
  const [form, setForm] = useState({
    task: "get_log",
    model: "holo4-27b",
    tier: "skills",
    timing: "freeze",
    max_steps: 30,
    time_budget: 180,
    max_cost_usd: "",
  });
  const refresh = () => {
    api<{ tasks: string[] }>("/status")
      .then((data) =>
        setAvailableTasks(
          data.tasks ?? ["get_log", "craft_planks", "craft_table", "ladder"],
        ),
      )
      .catch((e) => setError(e.message));
    api<Run[]>("/runs")
      .then(setRuns)
      .catch((e) => setError(e.message));
    api<Board[]>("/leaderboard")
      .then(setBoard)
      .catch((e) => setError(e.message));
  };
  useEffect(() => {
    let closed = false,
      socket: WebSocket,
      retry: ReturnType<typeof setTimeout>;
    function connect() {
      socket = new WebSocket(
        `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/live`,
      );
      socket.onopen = () => setWsConnected(true);
      socket.onclose = () => {
        setWsConnected(false);
        if (!closed) retry = setTimeout(connect, 1500);
      };
      socket.onmessage = (e) => {
        const { type, data } = JSON.parse(e.data);
        if (type === "snapshot") {
          setConnected(data.connected);
          setActive(data.active_run);
          setOverlay(data.overlay);
          setPhase(data.phase);
          if (data.active_run)
            api<Run>(`/runs/${data.active_run}`)
              .then((r) => {
                setSteps(r.steps);
                setSummary(r.summary);
                setInterventions(r.interventions);
                setRunActive(r.status === "running");
              })
              .catch((e) => setError(e.message));
        }
        if (type === "connection") setConnected(data.connected);
        if (type === "frame")
          setViewer(`data:image/jpeg;base64,${data.jpeg_base64}`);
        if (type === "agent_frame") {
          setModel(`data:image/png;base64,${data.png_base64}`);
          setModelStep(data.step);
        }
        if (type === "overlay") {
          setOverlay(data);
          setPhase(data.badges?.split(" ")[0] ?? "IDLE");
        }
        if (type === "step")
          setSteps((old) =>
            [...old.filter((s) => s.step !== data.step), data].sort(
              (a, b) => a.step - b.step,
            ),
          );
        if (type === "intervention") setInterventions((old) => [...old, data]);
        if (type === "summary") {
          setRunActive(false);
          setSummary(data);
          refresh();
        }
        if (type === "error") {
          setError(data.message);
          setRunActive(false);
        }
        if (type === "run_status") {
          setRunActive(data.status === "running");
          setActive(data.id);
          setPhase(data.status === "running" ? "STARTING" : "IDLE");
          if (data.status === "running") {
            setSteps([]);
            setSummary(null);
            setInterventions([]);
            setModel(undefined);
            setModelStep(-1);
          }
          refresh();
        }
      };
    }
    connect();
    refresh();
    return () => {
      closed = true;
      clearTimeout(retry);
      socket?.close();
    };
  }, []);
  async function start(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setConfirmation(null);
    try {
      const result = await api<{ id: string }>("/runs", {
        ...form,
        max_cost_usd: form.max_cost_usd ? Number(form.max_cost_usd) : null,
      });
      setActive(result.id);
      setRunActive(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function control(action: string) {
    setError("");
    try {
      const result = await api<{ confirmation_token: string; text: string }>(
        "/control",
        { action, source: "ui", text: goal },
      );
      if (action === "set_goal") setConfirmation(result);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function confirmGoal() {
    try {
      await api("/control/confirm-goal", {
        token: confirmation?.confirmation_token,
        source: "ui",
      });
      setConfirmation(null);
      setGoal("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function selectRun(id: string) {
    try {
      setReplay(await api<Run>(`/runs/${id}`));
      setReplayIndex(0);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const running = runActive;
  const current = steps.find((s) => s.step === modelStep);
  const replayStep = replay?.steps[replayIndex];
  return (
    <div className="app">
      <aside className="sidebar">
        <a className="brand" href="#" onClick={(e) => e.preventDefault()}>
          <span className="brand-symbol">◈</span>
          <span>
            OBSERVATORY<small>MINECRAFT · AGENT LAB</small>
          </span>
        </a>
        <p className="nav-label">WORKSPACE</p>
        <nav>
          {["Live", "Runs", "Leaderboard"].map((p, i) => (
            <button
              className={page === p ? "active" : ""}
              key={p}
              onClick={() => {
                setPage(p);
                refresh();
              }}
            >
              <span>{["◉", "▤", "▥"][i]}</span>
              {p}
              {p === "Live" && <i className={running ? "pulse" : ""} />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <span
            className={`connection-dot ${connected && wsConnected ? "online" : ""}`}
          />
          {connected && wsConnected
            ? "Minecraft connected"
            : "Connection pending"}
          <small>
            Local client · 640 × 480
            <br />
            Pixels in. Actions out.
          </small>
        </div>
      </aside>
      <main>
        <header>
          <div>
            <p className="eyebrow">MINECRAFT AGENT OBSERVATORY</p>
            <h1>
              {page === "Live"
                ? "A window into the agent’s world"
                : page === "Runs"
                  ? "Every run, every decision"
                  : "From trials to evidence"}
            </h1>
            <p className="subtitle">
              {page === "Live"
                ? "Watch the model think, act and make progress."
                : page === "Runs"
                  ? "Replay exact model observations and inspect the actions behind them."
                  : "Compare outcomes by model, tool tier and task. Smoke trials are not controlled rankings."}
            </p>
          </div>
          <span className="status-pill">
            <span className="connection-dot online" />
            {page === "Live" ? (overlay.badges ?? phase) : "LOCAL WORKSPACE"}
          </span>
        </header>
        {error && (
          <div className="error-banner" role="alert">
            {error}
            <button onClick={() => setError("")}>Dismiss</button>
          </div>
        )}
        {page === "Live" && (
          <>
            <div className="live-grid">
              <section className="panel viewer">
                <div className="section-head">
                  <h3>
                    <span className="live-dot" />
                    Live world
                  </h3>
                  <span>VIEWER · JPEG · ~5 FPS</span>
                </div>
                <Frame src={viewer} label="Spectator view · includes overlay" />
                <div className="viewer-caption">
                  <span className="tag">{form.model}</span>
                  <span>
                    {overlay.objective ?? "Ready for a new objective"}
                  </span>
                </div>
              </section>
              <section className="panel model">
                <div className="section-head">
                  <h3>Model’s view</h3>
                  <span>EXACT API INPUT</span>
                </div>
                <Frame
                  src={model}
                  record={current}
                  markers
                  label="Model observation · overlay excluded"
                />
                <div className="intent">
                  <span className="eyebrow">
                    DECLARED INTENT{" "}
                    {modelStep >= 0 ? `· STEP ${modelStep + 1}` : ""}
                  </span>
                  <p>
                    {current?.intent.join(" / ") ||
                      overlay.intent ||
                      "The agent will explain each action here."}
                  </p>
                  <small>
                    Cross = world aim. Numbered circles = GUI clicks. No
                    evaluator feedback reaches the model.
                  </small>
                </div>
              </section>
            </div>
            <Metrics steps={steps} summary={summary} />
            <div className="content-grid">
              <div>
                <Latency steps={steps} />
                <Timeline steps={steps} />
                <section className="panel">
                  <div className="section-head">
                    <h3>Interventions</h3>
                    <span>timestamped · source attributed</span>
                  </div>
                  {interventions.length ? (
                    interventions.map((v, i) => (
                      <div className="intervention" key={i}>
                        <time>
                          {new Date(v.timestamp).toLocaleTimeString()}
                        </time>
                        <span className="tag">{v.source}</span>
                        <strong>{title(v.action)}</strong>
                        <span>{v.text}</span>
                      </div>
                    ))
                  ) : (
                    <p className="muted">
                      No interventions. The agent is working independently.
                    </p>
                  )}
                </section>
              </div>
              <div>
                <Ladder steps={steps} summary={summary} />
                <section className="panel controls">
                  <div className="section-head">
                    <h3>Run controls</h3>
                    <span>between steps</span>
                  </div>
                  <div className="button-row">
                    <button
                      disabled={!running}
                      onClick={() => control("pause")}
                    >
                      Ⅱ Pause
                    </button>
                    <button
                      disabled={!running}
                      onClick={() => control("resume")}
                    >
                      ▷ Resume
                    </button>
                    <button
                      className="stop"
                      disabled={!running}
                      onClick={() => control("stop")}
                    >
                      □ Stop
                    </button>
                  </div>
                  <label>
                    Change objective{" "}
                    <small>Scoring target stays unchanged</small>
                    <textarea
                      value={goal}
                      onChange={(e) => {
                        setGoal(e.target.value);
                        setConfirmation(null);
                      }}
                      placeholder="Give the model a new instruction…"
                    />
                  </label>
                  <button
                    disabled={!running || !goal.trim()}
                    onClick={() => control("set_goal")}
                  >
                    Review new goal →
                  </button>
                  {confirmation && (
                    <div className="confirm">
                      <strong>Confirm objective change</strong>
                      <p>{confirmation.text}</p>
                      <button onClick={confirmGoal}>Confirm & apply</button>
                      <button onClick={() => setConfirmation(null)}>
                        Cancel
                      </button>
                    </div>
                  )}
                </section>
              </div>
            </div>
            <section className="panel start-panel">
              <div className="section-head">
                <h3>Launch a new run</h3>
                <span>one active run at a time</span>
              </div>
              <form onSubmit={start}>
                <label>
                  Task
                  <select
                    aria-label="Task"
                    value={form.task}
                    onChange={(e) => setForm({ ...form, task: e.target.value })}
                  >
                    {availableTasks.map((t) => (
                      <option key={t} value={t}>
                        {title(t)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Model
                  <input
                    aria-label="Model"
                    value={form.model}
                    onChange={(e) =>
                      setForm({ ...form, model: e.target.value })
                    }
                    list="models"
                  />
                  <datalist id="models">
                    <option value="holo4-27b" />
                    <option value="holo4-35b-a3b" />
                  </datalist>
                </label>
                <label>
                  Tool tier
                  <select
                    aria-label="Tool tier"
                    value={form.tier}
                    onChange={(e) => setForm({ ...form, tier: e.target.value })}
                  >
                    <option>skills</option>
                    <option>raw</option>
                  </select>
                </label>
                <label>
                  Timing
                  <select
                    aria-label="Timing"
                    value={form.timing}
                    onChange={(e) =>
                      setForm({ ...form, timing: e.target.value })
                    }
                  >
                    <option>freeze</option>
                    <option>realtime</option>
                  </select>
                </label>
                <label>
                  Step limit
                  <input
                    aria-label="Step limit"
                    type="number"
                    min="1"
                    max="10000"
                    value={form.max_steps}
                    onChange={(e) =>
                      setForm({ ...form, max_steps: Number(e.target.value) })
                    }
                  />
                </label>
                <label>
                  Seconds
                  <input
                    aria-label="Seconds"
                    type="number"
                    min="1"
                    max="10800"
                    value={form.time_budget}
                    onChange={(e) =>
                      setForm({ ...form, time_budget: Number(e.target.value) })
                    }
                  />
                </label>
                <label>
                  Cost cap ($)
                  <input
                    aria-label="Cost cap"
                    type="number"
                    min="0.0001"
                    step="0.01"
                    placeholder="Optional"
                    value={form.max_cost_usd}
                    onChange={(e) =>
                      setForm({ ...form, max_cost_usd: e.target.value })
                    }
                  />
                </label>
                <button
                  className="primary"
                  disabled={busy || running || !connected}
                  type="submit"
                >
                  {busy ? "Starting…" : "Start run ↗"}
                </button>
              </form>
            </section>
            <div className="integrations">
              <section className="panel placeholder">
                <span className="tag">COMING NEXT</span>
                <h3>Voice · Gladia</h3>
                <p>
                  Speak an intervention. Live transcription and confirmation
                  will appear here.
                </p>
                <button disabled>Connect microphone</button>
              </section>
              <DevinPanel runId={active} onTaskAdded={refresh} />
            </div>
          </>
        )}
        {page === "Runs" && (
          <>
            <section className="panel">
              <div className="section-head">
                <h3>Run archive</h3>
                <span>{runs.length} runs · select to replay</span>
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Run</th>
                      <th>Model / tier</th>
                      <th>Task</th>
                      <th>Outcome</th>
                      <th>Score</th>
                      <th>Cost</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {runs.map((r) => (
                      <tr key={r.id}>
                        <td>
                          <code>{r.id}</code>
                        </td>
                        <td>
                          {r.summary?.config.model ?? r.metadata.model}
                          <small>
                            {r.summary?.config.tier ?? r.metadata.tier}
                          </small>
                        </td>
                        <td>
                          {title(
                            r.summary?.task ?? r.metadata.task ?? "unknown",
                          )}
                        </td>
                        <td>
                          <span
                            className={`tag ${r.summary?.success ? "success" : ""}`}
                          >
                            {r.summary
                              ? r.summary.success
                                ? "SUCCESS"
                                : "NOT REACHED"
                              : r.status.toUpperCase()}
                          </span>
                        </td>
                        <td>{num((r.summary?.score ?? 0) * 100, 1)}%</td>
                        <td>{usd(r.summary?.total_cost_usd)}</td>
                        <td>
                          <button onClick={() => selectRun(r.id)}>
                            Replay →
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {runs.length === 0 && (
                  <p className="muted">
                    No runs yet. Start a trial on the Live page.
                  </p>
                )}
              </div>
            </section>
            {replay && (
              <>
                <section className="panel replay">
                  <div className="section-head">
                    <h3>Replay · {replay.id}</h3>
                    <span>{replay.steps.length} observations</span>
                  </div>
                  {replayStep ? (
                    <>
                      <div className="replay-grid">
                        <Frame
                          src={`/api/runs/${replay.id}/screenshots/${replayStep.step}`}
                          record={replayStep}
                          markers
                          label={`Exact model frame · step ${replayStep.step + 1}`}
                        />
                        <div>
                          <span className="eyebrow">DECLARED INTENT</span>
                          <h2>
                            {replayStep.intent.join(" / ") || "No tool call"}
                          </h2>
                          <p>
                            {num(replayStep.api_latency_ms)} ms ·{" "}
                            {usd(replayStep.cost_usd)}
                          </p>
                          <pre>
                            {JSON.stringify(replayStep.tool_calls, null, 2)}
                          </pre>
                          <h4>Executed inputs</h4>
                          <pre>
                            {JSON.stringify(
                              replayStep.executed_actions,
                              null,
                              2,
                            )}
                          </pre>
                          <div className="tags">
                            {replayStep.failure_tags.map((t) => (
                              <span className="tag danger" key={t}>
                                {t}
                              </span>
                            ))}
                          </div>
                          {replayStep.errors.map((e, i) => (
                            <p className="error" key={i}>
                              {e}
                            </p>
                          ))}
                        </div>
                      </div>
                      <div className="playback">
                        <button
                          disabled={replayIndex === 0}
                          onClick={() => setReplayIndex((i) => i - 1)}
                        >
                          ← Previous
                        </button>
                        <input
                          aria-label="Replay step"
                          type="range"
                          min="0"
                          max={replay.steps.length - 1}
                          value={replayIndex}
                          onChange={(e) =>
                            setReplayIndex(Number(e.target.value))
                          }
                        />
                        <span>
                          Step {replayIndex + 1} / {replay.steps.length}
                        </span>
                        <button
                          disabled={replayIndex === replay.steps.length - 1}
                          onClick={() => setReplayIndex((i) => i + 1)}
                        >
                          Next →
                        </button>
                      </div>
                    </>
                  ) : (
                    <p className="muted">No completed steps in this run.</p>
                  )}
                </section>
                <Metrics steps={replay.steps} summary={replay.summary} />
                <DevinPanel
                  runId={replay.id}
                  onTaskAdded={refresh}
                  onStep={(step) => {
                    const index = replay.steps.findIndex(
                      (s) => s.step === step,
                    );
                    if (index >= 0) {
                      setReplayIndex(index);
                      document
                        .querySelector(".replay")
                        ?.scrollIntoView({ behavior: "smooth" });
                    }
                  }}
                />
                <div className="content-grid">
                  <Timeline steps={replay.steps} />
                  <Ladder steps={replay.steps} summary={replay.summary} />
                </div>
              </>
            )}
          </>
        )}
        {page === "Leaderboard" && (
          <>
            <div className="board-intro">
              <span className="tag">STATE-VERIFIED OUTCOMES</span>
              <p>
                Grouped by model × tool tier × task. Latency aggregates
                individual steps; unknown pricing stays unknown.
              </p>
            </div>
            <section className="panel">
              <div className="section-head">
                <h3>Trial results</h3>
                <span>{board.length} configurations</span>
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Model / tier</th>
                      <th>Task</th>
                      <th>Runs</th>
                      <th>Success rate</th>
                      <th>Mean score</th>
                      <th>Mean / p95 latency</th>
                      <th>Cost / run</th>
                      <th>Time to milestone</th>
                      <th>Failure tags</th>
                    </tr>
                  </thead>
                  <tbody>
                    {board.map((b, i) => (
                      <tr key={i}>
                        <td>
                          <strong>{b.model}</strong>
                          <small>{b.tier}</small>
                        </td>
                        <td>{title(b.task)}</td>
                        <td>{b.runs}</td>
                        <td>
                          <div className="rate">
                            <span
                              style={{ width: `${b.success_rate * 100}%` }}
                            />
                          </div>
                          {num(b.success_rate * 100, 1)}%
                        </td>
                        <td>{num(b.mean_score * 100, 1)}%</td>
                        <td>
                          {num(b.mean_latency_ms)} / {num(b.p95_latency_ms)} ms
                        </td>
                        <td>{usd(b.cost_per_run)}</td>
                        <td>
                          {Object.entries(b.time_to_milestone).map(([r, t]) => (
                            <small key={r}>
                              {title(r)} · {num(t, 1)}s
                            </small>
                          ))}
                        </td>
                        <td>
                          {Object.entries(b.failure_counts).map(([t, n]) => (
                            <small className="failure-count" key={t}>
                              {t} ×{n}
                            </small>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {board.length === 0 && (
                  <p className="muted">
                    Finish a run to populate the leaderboard.
                  </p>
                )}
              </div>
            </section>
          </>
        )}
        <footer>
          OBSERVATORY{" "}
          <span>
            Local Minecraft Java · screenshot-only agents · privileged scoring
            stays server-side
          </span>
        </footer>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
