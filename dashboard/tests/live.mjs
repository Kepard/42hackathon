// Explicit opt-in: uses the local server's H Company credentials for one live run.
import { chromium, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

const out = resolve("../spike/ui");
await mkdir(out, { recursive: true });
const browser = await chromium.connectOverCDP("http://127.0.0.1:29229");
const page = await browser.contexts()[0].newPage();
await page.setViewportSize({ width: 1440, height: 1100 });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
try {
  await page.goto("http://127.0.0.1:5173");
  await expect(page.getByRole("button", { name: "Start run ↗" })).toBeEnabled({
    timeout: 15000,
  });
  await page.getByLabel("Model", { exact: true }).fill("holo4-27b");
  await page.getByLabel("Task", { exact: true }).selectOption("get_log");
  await page.getByLabel("Tool tier", { exact: true }).selectOption("skills");
  await page.getByLabel("Timing", { exact: true }).selectOption("freeze");
  await page.getByRole("button", { name: "Start run ↗" }).click();
  await expect(page.locator(".step").first()).toBeVisible({ timeout: 180000 });
  await page.getByRole("button", { name: "Ⅱ Pause" }).click();
  await expect(page.locator(".status-pill")).toContainText("PAUSED", {
    timeout: 20000,
  });
  await page.locator("textarea").fill("Obtain a log within three minutes.");
  await page.getByRole("button", { name: "Review new goal →" }).click();
  await expect(
    page.getByText("Confirm objective change", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Confirm & apply" }).click();
  await expect(
    page.locator(".intervention").filter({ hasText: "set goal" }),
  ).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: resolve(out, "live-mid-run.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "▷ Resume" }).click();
  await expect(page.getByRole("button", { name: "Start run ↗" })).toBeEnabled({
    timeout: 240000,
  });
  await page.locator("nav").getByRole("button", { name: "Runs" }).click();
  await page.getByRole("button", { name: "Replay →" }).first().click();
  await expect(
    page.getByRole("img", { name: /Exact model frame/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Next →", exact: true }).click();
  await expect(page.getByText(/Step 2 \/ /)).toBeVisible();
  await page.screenshot({
    path: resolve(out, "runs-replay.png"),
    fullPage: true,
  });
  await page
    .locator("nav")
    .getByRole("button", { name: "Leaderboard" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Trial results" }),
  ).toBeVisible();
  await expect(page.locator("tbody tr").first()).toBeVisible();
  await page.screenshot({
    path: resolve(out, "leaderboard.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
  console.log(
    "PASS UI: start live get_log / holo4-27b / skills / freeze; pause, confirm goal, resume; replay next frame; leaderboard; no browser errors",
  );
  console.log(out);
} finally {
  await browser.close();
}
