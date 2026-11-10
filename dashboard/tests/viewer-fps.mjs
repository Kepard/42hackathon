import { chromium, expect } from "@playwright/test";
const browser = await chromium.connectOverCDP("http://127.0.0.1:29229");
const page = await browser.contexts()[0].newPage();
let socket, timer;
try {
  await page.routeWebSocket("**/ws/live", ws => { socket = ws; });
  await page.goto("http://127.0.0.1:5173");
  await expect.poll(() => Boolean(socket)).toBe(true);
  const header = page.locator(".viewer").getByText(/^VIEWER/);
  const frame = () => socket.send(JSON.stringify({type:"frame",data:{jpeg_base64:""}}));
  timer = setInterval(frame,100);
  await expect(header).toHaveText(/JPEG · [1-9]\d* FPS$/);
  clearInterval(timer);
  await expect(header).toHaveText(/JPEG · 0 FPS$/, {timeout:4000});
  timer = setInterval(frame,100);
  await expect(header).toHaveText(/JPEG · [1-9]\d* FPS$/);
  socket.send(JSON.stringify({type:"connection",data:{connected:false}}));
  clearInterval(timer);
  await expect(header).toHaveText(/JPEG · 0 FPS$/);
  console.log("PASS viewer FPS:live frames, disabled/stale stream decay, resumed frames, mod disconnect reset");
} finally {
  clearInterval(timer);
  await page.close();
  await browser.close();
}
