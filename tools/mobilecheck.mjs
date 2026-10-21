// Mobile layout check through the Chrome DevTools Protocol (Node 22 built-in WebSocket, no packages).
//   node tools/mobilecheck.mjs <url> [<url> ...]   → JSON per page; exit 1 if any check fails
// Emulates a 390×844 phone (DPR 3, touch), then measures: horizontal overflow, smallest body text,
// tap targets under 32 px tall, images/elements wider than the viewport; and saves a full-page screenshot.
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const outDir = process.env.SHOTS || tmpdir();
const prof = mkdtempSync(join(tmpdir(), "margin-cdp-"));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--remote-debugging-port=9333", `--user-data-dir=${prof}`, "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let target;
for (let k = 0; k < 50 && !target; k++) {
  await sleep(200);
  try { target = (await (await fetch("http://127.0.0.1:9333/json")).json()).find((t) => t.type === "page"); } catch {}
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0; const pending = new Map(); const events = [];
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } else events.push(d); };
const cmd = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });

await cmd("Page.enable"); await cmd("Runtime.enable");
await cmd("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 3, mobile: true });
await cmd("Emulation.setTouchEmulationEnabled", { enabled: true });
await cmd("Emulation.setUserAgentOverride", { userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148" });
let failed = false; const results = [];
for (const [n, url] of process.argv.slice(2).entries()) {
  await cmd("Page.navigate", { url });
  await sleep(2500);
  const { result } = await cmd("Runtime.evaluate", { returnByValue: true, awaitPromise: true, expression: `(async () => {
    await document.fonts.ready;
    const vw = document.documentElement.clientWidth;
    const wide = [...document.querySelectorAll("body *")].filter(e => !e.closest(".skip")).filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && (r.right > vw + 1 || r.left < -1); })
      .slice(0, 5).map(e => e.tagName + "." + e.className + " " + Math.round(e.getBoundingClientRect().right));
    const texts = [...document.querySelectorAll(".exc p:not(.label), .standfirst, .end > p:not(.colophon):not(.endnav):not(.orn)")].map(e => parseFloat(getComputedStyle(e).fontSize));
    const taps = [...document.querySelectorAll("a, button")].filter(e => e.offsetParent && !e.closest(".exc, .also, .srcs, .ed ul, .colophon, h2, .skip, sup.c, .srcl, cite, .me h3, .toc"))
      .map(e => ({ t: (e.textContent || "").trim().slice(0, 30), h: Math.round(e.getBoundingClientRect().height) })).filter(x => x.h < 32);
    return { vw, scrollWidth: document.documentElement.scrollWidth, wide, minReadingPx: Math.min(...texts), smallTaps: taps.slice(0, 6),
             fonts: [...document.fonts].filter(f => f.status === "loaded").map(f => f.family + " " + f.style), height: document.documentElement.scrollHeight };
  })()` });
  const v = result.result.value ?? { error: JSON.stringify(result.exceptionDetails).slice(0, 300) };
  v.url = url;
  v.pass = { noHorizontalOverflow: v.scrollWidth <= v.vw && v.wide.length === 0, readingText16px: v.minReadingPx >= 16, tapTargets32px: v.smallTaps.length === 0,
             webFontsLoaded: v.fonts.length >= 2 };
  if (Object.values(v.pass).includes(false)) failed = true;
  await cmd("Emulation.setDeviceMetricsOverride", { width: 390, height: Math.min(v.height, 12000), deviceScaleFactor: 1, mobile: true });
  await sleep(400);
  const shot = await cmd("Page.captureScreenshot", { format: "png" });
  const f = join(outDir, `mobile-${n}.png`); writeFileSync(f, Buffer.from(shot.result.data, "base64")); v.screenshot = f;
  await cmd("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 3, mobile: true });
  results.push(v);
}
console.log(JSON.stringify(results, null, 1));
ws.close(); chrome.kill();
process.exit(failed ? 1 : 0);
