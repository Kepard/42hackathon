import { useEffect, useRef, useState, type Ref } from "react";
import {
  AnimatePresence,
  LayoutGroup,
  motion,
  MotionConfig,
} from "motion/react";
import { createRoot } from "react-dom/client";
import DevinPanel from "./DevinPanel";
import "cal-sans";
import "./style.css";
import { Collapse, ease, Panel, rise, stagger, Ticker } from "./ui";

type Call = { name: string; arguments: Record<string, unknown> };
type Step = {
  jev_calls?: Record<string, unknown>[];
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
  reflex?: string;
  reasoning_effort?: string | null;
  timing: string;
  label: string;
  median_time_seconds: number | null;
  median_steps: number | null;
  p50_api_latency_ms: number | null;
  infrastructure_failures: number;
  model: string;
  tier: string;
  task: string;
  runs: number;
  success_rate: number | null;
  valid_runs: number;
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
  imageRef,
}: {
  src?: string;
  record?: Step;
  label: string;
  markers?: boolean;
  imageRef?: Ref<HTMLImageElement>;
}) {
  const points =
    record?.tool_calls.filter(
      (c) => c.name === "click_at" || c.name === "shift_click_at",
    ) ?? [];
  return (
    <div className="frame">
      <div className="frame-label">{label}</div>
      {src ? (
        <img ref={imageRef} src={src} alt={label} />
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
            <motion.span
              key={`${record?.step}-${i}`}
              initial={{ scale: 0, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{
                type: "spring",
                stiffness: 500,
                damping: 24,
                delay: i * 0.06,
              }}
              className="marker click"
              title={`${c.arguments.x}, ${c.arguments.y}`}
              style={{
                left: `${Number(c.arguments.x) / 10}%`,
                top: `${Number(c.arguments.y) / 10}%`,
              }}
            >
              {i + 1}
            </motion.span>
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
    <Panel className="panel chart">
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
              className="grid-line"
            />
            <text x="0" y={119 - v * 90} className="axis" fontSize="10">
              {Math.round(v * max)}
            </text>
          </g>
        ))}
        {steps.length > 1 && (
          <polygon className="series-area" points={`40,115 ${pts} 580,115`} />
        )}
        <motion.polyline
          points={pts}
          fill="none"
          className="series"
          strokeWidth="2"
          strokeLinejoin="round"
          initial={{ pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: 0.9, ease }}
        />
        {steps.map((s, i) => (
          <motion.circle
            key={s.step}
            className="point"
            initial={{ r: 0 }}
            animate={{
              r: 3,
              cx: 40 + (i * 540) / Math.max(1, steps.length - 1),
              cy: 115 - (s.api_latency_ms / max) * 90,
            }}
            transition={{ duration: 0.35, ease }}
          >
            <title>
              Step {s.step + 1}: {num(s.api_latency_ms)} ms
            </title>
          </motion.circle>
        ))}
        <text x="40" y="140" className="axis" fontSize="10">
          Step 1
        </text>
        <text x="530" y="140" className="axis" fontSize="10">
          Step {steps.length}
        </text>
      </svg>
    </Panel>
  );
}
function Timeline({ steps }: { steps: Step[] }) {
  return (
    <Panel className="panel">
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
        <AnimatePresence initial={false}>
          {[...steps].reverse().map((s) => (
            <motion.details
              className="step"
              key={s.step}
              layout="position"
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, ease }}
            >
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
                  output {num(s.tokens.out)} · reasoning{" "}
                  {num(s.tokens.reasoning)} · {usd(s.cost_usd)} · action{" "}
                  {num(s.action_duration_ms)} ms
                </p>
                <h4>Tool calls</h4>
                <pre>{JSON.stringify(s.tool_calls, null, 2)}</pre>
                <h4>Executed inputs</h4>
                <pre>{JSON.stringify(s.executed_actions, null, 2)}</pre>
                {!!s.jev_calls?.length && <><h4>Jev pixel reflex decisions</h4><pre>{JSON.stringify(s.jev_calls, null, 2)}</pre></>}
                {s.errors.map((e, i) => (
                  <p className="error" key={i}>
                    {e}
                  </p>
                ))}
              </div>
            </motion.details>
          ))}
        </AnimatePresence>
      </div>
    </Panel>
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
    <motion.div className="metrics" variants={stagger}>
      {cards.map(([label, value, sub]) => (
        <motion.div className="metric" key={label} variants={rise}>
          <span>{label}</span>
          <Ticker value={value} />
          <small>{sub}</small>
        </motion.div>
      ))}
    </motion.div>
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
    <Panel className="panel">
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
              <motion.span
                className="rung-dot"
                key={completed.includes(r) ? "done" : "todo"}
                initial={completed.includes(r) ? { scale: 0.4 } : false}
                animate={{ scale: 1 }}
                transition={{ type: "spring", stiffness: 420, damping: 18 }}
              >
                {completed.includes(r) ? "✓" : i + 1}
              </motion.span>
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
    </Panel>
  );
}
function BenchmarkComparison({ board }: { board: Board[] }) {
  const [left, setLeft] = useState("baseline"),
    [right, setRight] = useState("after-fix");
  const labels = [
    ...new Set(["baseline", "after-fix", ...board.map((b) => b.label)]),
  ];
  const setups = new Map<string, Board[]>();
  for (const row of board.filter(
    (b) => b.label === left || b.label === right,
  )) {
    const key = JSON.stringify([row.task, row.model, row.tier, row.timing, row.reflex || "none", row.reasoning_effort || "default"]);
    setups.set(key, [...(setups.get(key) || []), row]);
  }
  function cell(row?: Board) {
    return row ? (
      <>
        <strong>
          {row.success_rate == null
            ? "—"
            : `${num(row.success_rate * 100, 1)}% success`}
        </strong>
        <small>
          n={row.runs} · {row.valid_runs} eligible ·{" "}
          {row.infrastructure_failures} infrastructure failures
        </small>
        <small>
          Median success time{" "}
          {row.median_time_seconds == null
            ? "—"
            : `${num(row.median_time_seconds, 1)}s`}{" "}
          · steps {row.median_steps ?? "—"}
        </small>
        <small>
          {usd(row.cost_per_run)} / run · p50 API{" "}
          {row.p50_api_latency_ms == null
            ? "—"
            : `${num(row.p50_api_latency_ms)}ms`}
        </small>
        <small>
          {Object.entries(row.failure_counts)
            .slice(0, 3)
            .map(([tag, n]) => `${tag} ×${n}`)
            .join(" · ") || "No failure tags"}
        </small>
      </>
    ) : (
      <span className="muted">No trials for this label yet</span>
    );
  }
  return (
    <Panel className="panel" aria-label="Matched benchmark comparison">
      <div className="section-head">
        <h3>Baseline vs after-fix</h3>
        <span>identical setups side by side</span>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Task / model / tier / timing</th>
              <th>
                <select
                  aria-label="First benchmark label"
                  value={left}
                  onChange={(e) => setLeft(e.target.value)}
                >
                  {labels.map((l) => (
                    <option key={l}>{l}</option>
                  ))}
                </select>
              </th>
              <th>
                <select
                  aria-label="Second benchmark label"
                  value={right}
                  onChange={(e) => setRight(e.target.value)}
                >
                  {labels.map((l) => (
                    <option key={l}>{l}</option>
                  ))}
                </select>
              </th>
            </tr>
          </thead>
          <tbody>
            {[...setups.entries()].map(([key, rows]) => (
              <tr key={key}>
                <td>
                  <strong>{title(rows[0].task)}</strong>
                  <small>
                    {rows[0].model} · {rows[0].tier} · {rows[0].timing} · reflex {rows[0].reflex || "none"} · reasoning {rows[0].reasoning_effort || "default"}
                  </small>
                </td>
                <td>{cell(rows.find((r) => r.label === left))}</td>
                <td>{cell(rows.find((r) => r.label === right))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!setups.size && (
        <p className="muted">
          Run a labelled benchmark to populate the comparison.
        </p>
      )}
    </Panel>
  );
}

function App() {
  const [page, setPage] = useState("Live");
  const [theme, setTheme] = useState(() =>
    document.documentElement.classList.contains("dark") ? "dark" : "light",
  );
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    try {
      localStorage.setItem("observatory-theme", theme);
    } catch {
      // Storage may be blocked; the theme still applies for this session.
    }
  }, [theme]);
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
  const viewer = useRef<string | undefined>(undefined);
  const viewerImage = useRef<HTMLImageElement>(null);
  const [viewerStarted, setViewerStarted] = useState(false);
  const [viewerFps, setViewerFps] = useState<number>();
  const viewerStats = useRef({ started: performance.now(), lastFrame: 0, frames: 0, ready: false });
  const [model, setModel] = useState<string>(),
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
    timing: "realtime",
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
        setViewerFps(0);
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
        if (type === "connection") {
          setConnected(data.connected);
          if (!data.connected) setViewerFps(0);
        }
        if (type === "frame") {
          viewer.current = `data:image/jpeg;base64,${data.jpeg_base64}`;
          if (viewerImage.current) viewerImage.current.src = viewer.current;
          else if (!viewerStats.current.ready) {
            viewerStats.current.ready = true;
            setViewerStarted(true);
          }
          const now = performance.now(), stats = viewerStats.current;
          stats.lastFrame = now;
          stats.frames++;
          if (now - stats.started >= 1000) {
            setViewerFps(stats.frames * 1000 / (now - stats.started));
            stats.frames = 0;
            stats.started = now;
          }
        }
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
    const staleViewer = setInterval(() => {
      const now = performance.now(), stats = viewerStats.current;
      if (now - stats.lastFrame >= 2000) {
        setViewerFps(0);
        stats.frames = 0;
        stats.started = now;
      }
    }, 500);
    return () => {
      closed = true;
      clearInterval(staleViewer);
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
    <MotionConfig reducedMotion="user">
      <div className="app">
        <aside className="sidebar">
          <a className="brand" href="#" onClick={(e) => e.preventDefault()}>
            <span className="brand-symbol" aria-hidden />
            <span>
              Observatory<small>Minecraft agent lab</small>
            </span>
          </a>
          <p className="nav-label">Workspace</p>
          <LayoutGroup>
            <nav>
              {["Live", "Runs", "Leaderboard"].map((p, i) => (
                <motion.button
                  className={page === p ? "active" : ""}
                  key={p}
                  whileTap={{ scale: 0.97 }}
                  onClick={() => {
                    setPage(p);
                    refresh();
                  }}
                >
                  {page === p && (
                    <motion.span
                      className="nav-active"
                      layoutId="nav-active"
                      transition={{
                        type: "spring",
                        stiffness: 500,
                        damping: 38,
                      }}
                    />
                  )}
                  <span className="nav-icon" aria-hidden>
                    {["◉", "▤", "▥"][i]}
                  </span>
                  <span>{p}</span>
                  {p === "Live" && <i className={running ? "pulse" : ""} />}
                </motion.button>
              ))}
            </nav>
          </LayoutGroup>
          <div className="sidebar-bottom">
            <div className="sidebar-status">
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
            <button
              className="theme-toggle"
              aria-label="Toggle dark mode"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            >
              <span>{theme === "dark" ? "Dark" : "Light"} theme</span>
              <span aria-hidden>{theme === "dark" ? "☾" : "☀"}</span>
            </button>
          </div>
        </aside>
        <main>
          <header>
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={page}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.25, ease }}
              >
                <p className="eyebrow">Minecraft agent observatory</p>
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
              </motion.div>
            </AnimatePresence>
            <span className="status-pill">
              <span className="connection-dot online" />
              {page === "Live" ? (overlay.badges ?? phase) : "LOCAL WORKSPACE"}
            </span>
          </header>
          <Collapse show={!!error}>
            <div className="error-banner" role="alert">
              {error}
              <button onClick={() => setError("")}>Dismiss</button>
            </div>
          </Collapse>
          <AnimatePresence mode="wait">
            <motion.div
              key={page}
              variants={stagger}
              initial="hidden"
              animate="show"
              exit={{ opacity: 0, y: -8, transition: { duration: 0.15 } }}
            >
              {page === "Live" && (
                <>
                  <div className="live-grid">
                    <Panel className="panel viewer">
                      <div className="section-head">
                        <h3>
                          <span className="live-dot" />
                          Live world
                        </h3>
                        <span>VIEWER · JPEG · {viewerFps?.toFixed(0) ?? "30 TARGET"} FPS</span>
                      </div>
                      <Frame
                        src={viewerStarted ? viewer.current : undefined}
                        imageRef={viewerImage}
                        label="Spectator view · includes overlay"
                      />
                      <div className="viewer-caption">
                        <span className="tag">{form.model}</span>
                        <span>
                          {overlay.objective ?? "Ready for a new objective"}
                        </span>
                      </div>
                    </Panel>
                    <Panel className="panel model">
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
                          Declared intent{" "}
                          {modelStep >= 0 ? `· step ${modelStep + 1}` : ""}
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
                    </Panel>
                  </div>
                  <Metrics steps={steps} summary={summary} />
                  <div className="content-grid">
                    <div>
                      <Latency steps={steps} />
                      <Timeline steps={steps} />
                      <Panel className="panel">
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
                            No interventions. The agent is working
                            independently.
                          </p>
                        )}
                      </Panel>
                    </div>
                    <div>
                      <Ladder steps={steps} summary={summary} />
                      <Panel className="panel controls">
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
                        <Collapse show={!!confirmation}>
                          {confirmation && (
                            <div className="confirm">
                              <strong>Confirm objective change</strong>
                              <p>{confirmation.text}</p>
                              <button onClick={confirmGoal}>
                                Confirm & apply
                              </button>
                              <button onClick={() => setConfirmation(null)}>
                                Cancel
                              </button>
                            </div>
                          )}
                        </Collapse>
                      </Panel>
                    </div>
                  </div>
                  <Panel className="panel start-panel">
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
                          onChange={(e) =>
                            setForm({ ...form, task: e.target.value })
                          }
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
                          onChange={(e) =>
                            setForm({ ...form, tier: e.target.value })
                          }
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
                            setForm({
                              ...form,
                              max_steps: Number(e.target.value),
                            })
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
                            setForm({
                              ...form,
                              time_budget: Number(e.target.value),
                            })
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
                      <motion.button
                        className="primary"
                        disabled={busy || running || !connected}
                        type="submit"
                        whileTap={{ scale: 0.97 }}
                      >
                        {busy ? "Starting…" : "Start run ↗"}
                      </motion.button>
                    </form>
                  </Panel>
                  <div className="integrations">
                    <Panel className="panel placeholder">
                      <span className="tag">COMING NEXT</span>
                      <h3>Voice · Gladia</h3>
                      <p>
                        Speak an intervention. Live transcription and
                        confirmation will appear here.
                      </p>
                      <button disabled>Connect microphone</button>
                    </Panel>
                    <DevinPanel runId={active} onTaskAdded={refresh} />
                  </div>
                </>
              )}
              {page === "Runs" && (
                <>
                  <Panel className="panel">
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
                                  r.summary?.task ??
                                    r.metadata.task ??
                                    "unknown",
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
                  </Panel>
                  {replay && (
                    <>
                      <Panel className="panel replay">
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
                                <span className="eyebrow">Declared intent</span>
                                <motion.h2
                                  key={replayStep.step}
                                  initial={{ opacity: 0, y: 6 }}
                                  animate={{ opacity: 1, y: 0 }}
                                  transition={{ duration: 0.25, ease }}
                                >
                                  {replayStep.intent.join(" / ") ||
                                    "No tool call"}
                                </motion.h2>
                                <p>
                                  {num(replayStep.api_latency_ms)} ms ·{" "}
                                  {usd(replayStep.cost_usd)}
                                </p>
                                <pre>
                                  {JSON.stringify(
                                    replayStep.tool_calls,
                                    null,
                                    2,
                                  )}
                                </pre>
                                {!!replayStep.jev_calls?.length && <><h4>Jev pixel reflex decisions</h4><pre>{JSON.stringify(replayStep.jev_calls, null, 2)}</pre></>}
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
                                disabled={
                                  replayIndex === replay.steps.length - 1
                                }
                                onClick={() => setReplayIndex((i) => i + 1)}
                              >
                                Next →
                              </button>
                            </div>
                          </>
                        ) : (
                          <p className="muted">
                            No completed steps in this run.
                          </p>
                        )}
                      </Panel>
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
                  <BenchmarkComparison board={board} />
                  <motion.div className="board-intro" variants={rise}>
                    <span className="tag">STATE-VERIFIED OUTCOMES</span>
                    <p>
                      Matched task × model × tier × timing, with labels adjacent
                      for baseline / after-fix comparison. Time is median
                      successful-trial time; steps include unsuccessful trials.
                      Infrastructure failures are excluded from success rate and
                      medians, not hidden from n or cost.
                    </p>
                  </motion.div>
                  <Panel className="panel">
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
                            <th>Timing / label</th>
                            <th>Runs</th>
                            <th>Success rate</th>
                            <th>Median time / steps</th>
                            <th>p50 API latency</th>
                            <th>Cost / run</th>
                            <th>Infrastructure failures</th>
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
                              <td>
                                <strong>{b.label}</strong>
                                <small>{b.timing} · reflex {b.reflex || "none"} · reasoning {b.reasoning_effort || "default"}</small>
                              </td>
                              <td>
                                {b.runs}
                                <small>{b.valid_runs} eligible</small>
                              </td>
                              <td>
                                <div className="rate">
                                  <motion.span
                                    initial={{ width: 0 }}
                                    animate={{
                                      width: `${(b.success_rate ?? 0) * 100}%`,
                                    }}
                                    transition={{ duration: 0.7, ease }}
                                  />
                                </div>
                                {b.success_rate == null
                                  ? "—"
                                  : `${num(b.success_rate * 100, 1)}%`}
                              </td>
                              <td>
                                {b.median_time_seconds == null
                                  ? "—"
                                  : `${num(b.median_time_seconds, 1)}s`}{" "}
                                /{" "}
                                {b.median_steps == null
                                  ? "—"
                                  : num(b.median_steps, 1)}
                              </td>
                              <td>
                                {b.p50_api_latency_ms == null
                                  ? "—"
                                  : `${num(b.p50_api_latency_ms)} ms`}
                              </td>
                              <td>{usd(b.cost_per_run)}</td>
                              <td>{b.infrastructure_failures}</td>
                              <td>
                                {Object.entries(b.failure_counts)
                                  .slice(0, 4)
                                  .map(([t, n]) => (
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
                  </Panel>
                </>
              )}
            </motion.div>
          </AnimatePresence>
          <footer>
            Observatory{" "}
            <span>
              Local Minecraft Java · screenshot-only agents · privileged scoring
              stays server-side
            </span>
          </footer>
        </main>
      </div>
    </MotionConfig>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
