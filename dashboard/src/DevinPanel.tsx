import { useEffect, useState } from "react";
import { Panel } from "./ui";

type Task = Record<string, unknown>;
type Investigation = {
  headline: string;
  failure_category: string;
  root_cause: string;
  confidence: number;
  evidence: { step: number; observation: string }[];
  suggested_fixes: { area: string; change: string; expected_effect: string }[];
};
type Result = Partial<Investigation> & {
  task?: Task;
  test_summary?: string;
  pr_url?: string;
  summary?: string;
  tests_added?: string;
  risk?: string;
};
type Job = {
  id: string;
  kind: string;
  run_id: string | null;
  status: string;
  status_detail?: string;
  url?: string;
  done: boolean;
  error?: string;
  result: Result | null;
  added_locally?: string;
};

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    `/api/devin${path}`,
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
    throw new Error(
      typeof data.detail === "string" ? data.detail : "Devin request failed",
    );
  return data;
}
function External({
  url,
  children,
}: {
  url?: string;
  children: React.ReactNode;
}) {
  if (!url || !/^https:\/\/(?:app\.devin\.ai|github\.com)\//.test(url))
    return null;
  return (
    <a href={url} target="_blank" rel="noreferrer">
      {children} ↗
    </a>
  );
}

export default function DevinPanel({
  runId,
  onStep,
  onTaskAdded,
}: {
  runId?: string | null;
  onStep?: (step: number) => void;
  onTaskAdded?: () => void;
}) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const saved = await request<Job[]>(
          `/jobs${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`,
        );
        const updated = await Promise.all(
          saved
            .slice(0, 20)
            .map((job) => (job.done ? job : request<Job>(`/jobs/${job.id}`))),
        );
        if (alive) {
          setJobs(updated);
          setPollError("");
        }
      } catch (e) {
        if (alive) setPollError((e as Error).message);
      }
      if (alive) timer = setTimeout(poll, 4000);
    }
    setJobs([]);
    poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [runId]);
  async function start(
    kind: string,
    investigation_id?: string,
    fix_index?: number,
  ) {
    if (
      kind !== "investigate" &&
      !window.confirm(
        "This asks Devin to change code, add tests and open a PR. Continue?",
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      const job = await request<Job>("/jobs", {
        kind,
        run_id: runId || null,
        description,
        investigation_id,
        fix_index,
      });
      setJobs((old) => [job, ...old.filter((j) => j.id !== job.id)]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function add(job: Job) {
    setBusy(true);
    setError("");
    try {
      await request(`/jobs/${job.id}/add-task`, {});
      setJobs((old) =>
        old.map((j) =>
          j.id === job.id
            ? { ...j, added_locally: new Date().toISOString() }
            : j,
        ),
      );
      onTaskAdded?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Panel className="panel devin-panel" aria-label="Devin engineering copilot">
      <div className="section-head">
        <h3>Devin engineering copilot</h3>
        <span>ON DEMAND · RECORDED EVIDENCE ONLY</span>
      </div>
      <p className="muted">
        Outside the gameplay loop. Interpretability means observable intents,
        actions and outcomes — not hidden reasoning.
      </p>
      <label>
        Describe a challenge
        <textarea
          aria-label="Challenge description"
          placeholder="Smelt iron within 10 minutes starting with a stone pickaxe…"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          maxLength={4000}
        />
      </label>
      <div className="button-row">
        <button
          disabled={busy || !description.trim()}
          onClick={() => start("challenge")}
        >
          Create Challenge · opens PR
        </button>
        <button disabled={busy || !runId} onClick={() => start("investigate")}>
          Investigate Run
        </button>
        {busy && <span className="muted">Sending request…</span>}
      </div>
      {!runId && (
        <p className="muted">Open a recorded run in Runs to investigate it.</p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {pollError && (
        <p className="error" role="alert">
          Status refresh failed: {pollError}. Retrying…
        </p>
      )}
      {jobs.length === 0 && (
        <p className="muted">No engineering sessions yet.</p>
      )}
      {jobs.map((job) => (
        <article className="devin-job" key={job.id} data-job-id={job.id}>
          <div className="section-head">
            <h4>
              {job.kind === "investigate"
                ? "Run investigation"
                : job.kind === "challenge"
                  ? "Challenge proposal"
                  : "Fix review"}
            </h4>
            <span className="tag">
              {job.status}
              {job.status_detail ? ` · ${job.status_detail}` : ""}
            </span>
          </div>
          <p className="muted">
            {job.run_id && `Run ${job.run_id} · `}
            <External url={job.url}>Open Devin session</External>
          </p>
          {job.error && <p className="error">{job.error}</p>}
          {!job.done && (
            <p className="muted">
              Polling session every four seconds. Safe to leave this page;
              results are saved.
            </p>
          )}
          {job.result && (
            <>
              {job.result.headline && (
                <>
                  <h3>{job.result.headline}</h3>
                  <div className="tags">
                    <span className="tag">{job.result.failure_category}</span>
                    <span className="tag">
                      Confidence{" "}
                      {Math.round((job.result.confidence ?? 0) * 100)}%
                    </span>
                  </div>
                  <p className="devin-prose">{job.result.root_cause}</p>
                  <h4>Recorded evidence</h4>
                  <ul className="devin-evidence">
                    {job.result.evidence?.map((e, i) => (
                      <li key={i}>
                        {onStep ? (
                          <button onClick={() => onStep(e.step)}>
                            Step {e.step + 1}
                          </button>
                        ) : (
                          <span>Step {e.step + 1}</span>
                        )}{" "}
                        {e.observation}
                      </li>
                    ))}
                  </ul>
                  <h4>Suggested fixes</h4>
                  <p className="muted">
                    Unverified proposals, not implemented changes. Review must
                    preserve screenshot-only agent inputs; privileged state must
                    not be fed back to the model.
                  </p>
                  {job.result.suggested_fixes?.map((fix, index) => (
                    <div className="devin-fix" key={index}>
                      <span className="tag">{fix.area}</span>
                      <p>{fix.change}</p>
                      <p className="muted">Expected: {fix.expected_effect}</p>
                      <button
                        disabled={busy || !job.done}
                        onClick={() => start("review_fix", job.id, index)}
                      >
                        Review Fix {index + 1} · opens PR
                      </button>
                    </div>
                  ))}
                </>
              )}
              {job.result.task && (
                <>
                  <h4>Proposed task</h4>
                  <pre>{JSON.stringify(job.result.task, null, 2)}</pre>
                  <p>{job.result.test_summary}</p>
                  <button
                    disabled={busy || !job.done || !!job.added_locally}
                    onClick={() => add(job)}
                  >
                    {job.added_locally ? "Added locally ✓" : "Add locally"}
                  </button>
                </>
              )}
              {job.result.summary && <p>{job.result.summary}</p>}
              {job.result.tests_added && (
                <p>
                  <strong>Tests:</strong> {job.result.tests_added}
                </p>
              )}
              {job.result.risk && (
                <p>
                  <strong>Risk:</strong> {job.result.risk}
                </p>
              )}
              <External url={job.result.pr_url}>Open pull request</External>
            </>
          )}
        </article>
      ))}
    </Panel>
  );
}
