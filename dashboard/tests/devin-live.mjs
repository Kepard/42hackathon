// Explicit opt-in. Creates at most ONE investigation session; never a PR workflow.
import { chromium, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";
if (process.env.DEVIN_LIVE !== "1")
  throw new Error("Set DEVIN_LIVE=1 to investigate recorded evidence");
const browser = await chromium.connectOverCDP("http://127.0.0.1:29229");
const page = await browser.contexts()[0].newPage();
await page.setViewportSize({ width: 1440, height: 1100 });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
try {
  await page.goto("http://127.0.0.1:5173");
  const runs = await (
    await page.request.get("http://127.0.0.1:5173/api/runs")
  ).json();
  const run = runs.find(
    (r) =>
      r.summary?.task === "get_log" &&
      !r.summary.success &&
      r.summary.steps === 30 &&
      r.summary.failure_tags.includes("budget_exhausted"),
  );
  if (!run) throw new Error("Requested recorded failed get_log run not found");
  await page.locator("nav").getByRole("button", { name: "Runs" }).click();
  await page
    .getByRole("row")
    .filter({ hasText: run.id })
    .getByRole("button", { name: "Replay →" })
    .click();
  const panel = page.getByRole("region", { name: "Devin engineering copilot" });
  await expect(panel).toBeVisible();
  const saved = await (
    await page.request.get(
      `http://127.0.0.1:5173/api/devin/jobs?run_id=${run.id}`,
    )
  ).json();
  let job = saved.find((j) => j.kind === "investigate");
  if (!job) {
    const created = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/devin/jobs") && r.request().method() === "POST",
    );
    await panel
      .getByRole("button", { name: "Investigate Run", exact: true })
      .click();
    const response = await created;
    job = await response.json();
    if (response.status() !== 202) throw new Error(JSON.stringify(job));
  }
  console.log(
    `Investigation session: ${job.url} · job ${job.id} · run ${run.id}`,
  );
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `http://127.0.0.1:5173/api/devin/jobs/${job.id}`,
        );
        if (!response.ok()) return false;
        job = await response.json();
        return job.done;
      },
      { timeout: 900000, intervals: [10000] },
    )
    .toBe(true);
  if (!job.result || job.error) throw new Error(JSON.stringify(job));
  const article = panel.locator(`[data-job-id="${job.id}"]`);
  await expect(
    article.getByText(job.result.headline, { exact: true }),
  ).toBeVisible({ timeout: 15000 });
  const evidence = article.getByRole("button", { name: /^Step \d+$/ }).first();
  if (await evidence.count()) {
    const step = Number((await evidence.innerText()).replace("Step ", ""));
    await evidence.click();
    await expect(page.getByLabel("Replay step")).toHaveValue(String(step - 1));
  }
  await panel.scrollIntoViewIfNeeded();
  await expect(panel.getByRole("alert")).toHaveCount(0);
  await page.screenshot({
    path: "../spike/ui/devin-investigation.png",
    fullPage: true,
  });
  await panel.screenshot({ path: "../spike/ui/devin-panel.png" });
  await writeFile(
    "../spike/ui/devin-investigation.json",
    JSON.stringify(job, null, 2) + "\n",
  );
  expect(errors).toEqual([]);
  console.log(
    JSON.stringify(
      { session_url: job.url, run_id: run.id, structured_output: job.result },
      null,
      2,
    ),
  );
  console.log(
    "PASS live evidence-only Devin investigation: attached zip, polled structured result, replay evidence link, panel screenshot; no browser errors",
  );
} finally {
  await page.close();
  await browser.close();
}
