// Paged-reader check through Chrome DevTools: node tools/pagecheck.mjs <url>
// For desktop (1440×900, spread), tablet (1024×1366) and phone (390×844, pages mode switched on): counts pages, checks no page
// overflows, contents numbers point at the right page, next/prev buttons, arrow keys, swipe, #p= links, the final page, and the toggle.
import { spawn } from "node:child_process"; import { mkdtempSync } from "node:fs"; import { tmpdir } from "node:os"; import { join } from "node:path";
const url = process.argv[2];
const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", ["--headless=new", "--disable-gpu", "--remote-debugging-port=9335",
  `--user-data-dir=${mkdtempSync(join(tmpdir(), "pc-"))}`, "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let t; for (let k = 0; k < 50 && !t; k++) { await sleep(200); try { t = (await (await fetch("http://127.0.0.1:9335/json")).json()).find((x) => x.type === "page"); } catch {} }
const ws = new WebSocket(t.webSocketDebuggerUrl); await new Promise((r) => (ws.onopen = r));
let id = 0; const P = new Map(); ws.onmessage = (m) => { const d = JSON.parse(m.data); if (P.has(d.id)) { P.get(d.id)(d); P.delete(d.id); } };
const cmd = (method, params = {}) => new Promise((r) => { const i = ++id; P.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async (e) => { const r = await cmd("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true }); return r.result.result?.value ?? r.result.exceptionDetails?.text; };
await cmd("Page.enable");
const pos = () => ev(`document.querySelector(".rb-pos")?.textContent`);
const out = {}; let fail = false;
for (const [name, w, h, mobile] of [["desktop", 1440, 900, false], ["tablet", 1024, 1366, false], ["phone", 390, 844, true]]) {
  await cmd("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile });
  await cmd("Emulation.setTouchEmulationEnabled", { enabled: mobile });
  await cmd("Page.navigate", { url }); await sleep(1800);
  const defaultMode = await ev(`document.querySelector(".book") ? "pages" : "scroll"`);
  if (defaultMode === "scroll") { await ev(`document.querySelector(".rb-mode").click()`); await sleep(800); }
  const r = { defaultMode };
  r.pages = await ev(`document.querySelectorAll(".pg").length`);
  r.spread = await ev(`document.querySelector(".book").classList.contains("spread")`);
  r.visibleAtStart = await ev(`[...document.querySelectorAll(".pg")].filter(p => !p.hidden).length`);
  r.overflowing = await ev(`[...document.querySelectorAll(".pg-in")].filter(i => !i.classList.contains("tall") && i.scrollHeight > i.clientHeight + 1).length`);
  r.tallPages = await ev(`document.querySelectorAll(".pg-in.tall").length`);
  r.nearlyEmpty = await ev(`[...document.querySelectorAll(".pg-in")].slice(0, -1).filter(i => { const b = [...i.children].reduce((s, c) => s + c.getBoundingClientRect().height, 0); return b < i.clientHeight * 0.35; }).length`);
  r.toc = await ev(`[...document.querySelectorAll(".book [data-toc]")].map(a => { const n = +a.querySelector(".pn").textContent; const pg = document.querySelector(".book #s-" + a.dataset.toc)?.closest(".pg"); return a.dataset.toc + ":" + n + (pg && pg.id === "p" + n ? "✓" : "✗"); })`);
  await ev(`document.querySelector(".rb-next").click()`); r.afterNext = await pos();
  await cmd("Input.dispatchKeyEvent", { type: "keyDown", key: "ArrowRight", code: "ArrowRight", windowsVirtualKeyCode: 39 }); await cmd("Input.dispatchKeyEvent", { type: "keyUp", key: "ArrowRight", code: "ArrowRight", windowsVirtualKeyCode: 39 });
  r.afterArrowRight = await pos();
  await ev(`document.querySelector(".rb-prev").click()`); r.afterPrev = await pos();
  if (mobile) {
    await cmd("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: 320, y: 400 }] });
    await cmd("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: 120, y: 405 }] });
    await cmd("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] }); await sleep(200);
    r.afterSwipeLeft = await pos();
  }
  await ev(`(() => { const a = document.querySelector('.book [data-toc="end"]'); a.click(); })()`); await sleep(200);
  r.contentsToEnd = await pos();
  r.lastPageIsEnd = await ev(`(() => { const ps = [...document.querySelectorAll(".pg")]; const last = ps[ps.length - 1]; return !!last.querySelector(".end") && !last.hidden; })()`);
  r.nextDisabledAtEnd = await ev(`document.querySelector(".rb-next").disabled`);
  await ev(`document.querySelector(".rb-mode").click()`); await sleep(300);
  r.toggleToScroll = await ev(`!document.querySelector(".book") && !document.querySelector(".paper").hidden`);
  await ev(`document.querySelector(".rb-mode").click()`); await sleep(500);
  r.pass = r.overflowing === 0 && r.toc.every((x) => x.endsWith("✓")) && r.lastPageIsEnd && r.nextDisabledAtEnd && r.toggleToScroll
    && (name !== "desktop" || (r.spread && r.visibleAtStart === 2)) && (name !== "phone" || r.defaultMode === "scroll")
    && r.afterPrev === r.afterNext && r.afterArrowRight !== r.afterNext && (!mobile || r.afterSwipeLeft !== r.afterPrev);
  if (!r.pass) fail = true;
  out[name] = r;
  await ev(`localStorage.clear()`);
}
console.log(JSON.stringify(out, null, 1)); ws.close(); chrome.kill(); process.exit(fail ? 1 : 0);
