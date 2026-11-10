import { chromium, expect } from "@playwright/test";
const browser = await chromium.connectOverCDP("http://127.0.0.1:29229");
const page = await browser.contexts()[0].newPage();
const controls = [];
try {
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/control")
      controls.push(route.request().postDataJSON().action);
    return route.fulfill({
      json:
        path === "/api/runs/mock-run"
          ? { status: "running", steps: [], summary: null, interventions: [] }
          : [],
    });
  });
  await page.routeWebSocket("**/ws/live", (ws) =>
    ws.send(
      JSON.stringify({
        type: "snapshot",
        data: {
          connected: true,
          active_run: "mock-run",
          phase: "PAUSED FROZEN",
          overlay: { badges: "PAUSED FROZEN" },
        },
      }),
    ),
  );
  await page.goto("http://127.0.0.1:5173");
  await expect(page.getByRole("button", { name: "▷ Resume" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "□ Stop" })).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Start run ↗" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "▷ Resume" }).click();
  await page.getByRole("button", { name: "□ Stop" }).click();
  expect(controls).toEqual(["resume", "stop"]);
  console.log(
    "PASS offline UI: PAUSED FROZEN active run can resume and stop; new run disabled",
  );
} finally {
  await page.close();
  await browser.close();
}
