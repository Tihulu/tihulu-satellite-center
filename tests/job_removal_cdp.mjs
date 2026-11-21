#!/usr/bin/env node
// Interactive DOM regression test for the native-mode scheduled-job removal UI
// (the x button and the "Clear N finished job(s)" control), driven in a real
// Chromium over the DevTools Protocol.
//
// It reproduces the Qt WebChannel bridge with a stub that records commands and
// answers from a canned schedule store, so the native code path runs in a plain
// browser without installing anything:
//
//   node tests/job_removal_cdp.mjs [appRoot]
//
//   appRoot   directory whose parent contains the `satellite_center` package
//             (default: ../usr/share/tihulu-satellite-center)
//
// Requirements: node >= 22 (global WebSocket) and chromium/chromium-browser on
// PATH. Nothing else is installed; the test spawns its own server and browser.
//
// Verified behaviour:
//   * the armed-jobs list renders a per-card x and a "Clear N finished job(s)"
//     control that only counts non-pending records;
//   * clicking x on a finished record calls clear_job with that exact id;
//   * clicking the bulk control calls clear_finished_jobs;
//   * after either action the record disappears from the list AND from the
//     native calendar (the calendar no longer carries the job id), while pending
//     schedules stay untouched.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const APP_ROOT = process.argv[2] || "../usr/share/tihulu-satellite-center";
const PORT = 8830;
// A fresh CDP port per run: stale browser instances must never be adopted.
const CDP = 9400 + Math.floor(Math.random() * 400);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(...a);
let failures = 0;
const check = (name, ok, detail = "") => {
  log((ok ? "PASS " : "FAIL ") + name + (detail ? " :: " + detail : ""));
  if (!ok) failures++;
};

const cleanup = [];
const run = async () => {
  // ---- start the app's web UI -------------------------------------------------
  const data = mkdtempSync(join(tmpdir(), "sat-cdp-"));
  const home = mkdtempSync(join(tmpdir(), "sat-cdp-home-"));
  cleanup.push(data, home);
  const server = spawn("python3", ["-m", "satellite_center", "serve", "--host", "127.0.0.1",
    "--port", String(PORT)], {
    env: { ...process.env, PYTHONPATH: APP_ROOT, HOME: home, TMPDIR: data,
           TIHULU_SATELLITE_DATA: data, PYTHONDONTWRITEBYTECODE: "1" },
    stdio: ["ignore", "ignore", "pipe"], detached: false,
  });
  cleanup.push(server);
  server.stderr.on("data", (d) => process.stderr.write("[serve] " + d));
  let up = false;
  for (let i = 0; i < 180; i++) {           // cold imports (numpy/skyfield) can take a while
    try { const r = await fetch(`http://127.0.0.1:${PORT}/browser/index.html`); if (r.ok) { up = true; break; } } catch {}
    await sleep(500);
  }
  if (!up) throw new Error(`serve never answered on ${PORT} (appRoot=${APP_ROOT})`);
  const profile = mkdtempSync(join(tmpdir(), "sat-cdp-prof-"));
  cleanup.push(profile);
  const browser = spawn("chromium-browser", [
    "--headless", "--no-sandbox", "--disable-gpu",
    `--remote-debugging-port=${CDP}`, `--user-data-dir=${profile}`,
    `http://127.0.0.1:${PORT}/browser/index.html`,
  ], { stdio: "ignore" });
  cleanup.push(browser);
  await sleep(7000);

  // ---- CDP client -------------------------------------------------------------
  const list = await (await fetch(`http://127.0.0.1:${CDP}/json/list`)).json();
  const target = list.find((t) => t.type === "page");
  if (!target) throw new Error("no chromium page target");
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.method === "Fetch.requestPaused") {
      const { requestId, request } = m.params;
      ws.send(JSON.stringify({ id: ++id, method: request.url.startsWith("qrc:///qtwebchannel/qwebchannel.js")
        ? "Fetch.fulfillRequest" : "Fetch.continueRequest",
        params: request.url.startsWith("qrc:///qtwebchannel/qwebchannel.js")
          ? { requestId, responseCode: 200,
              responseHeaders: [{ name: "Content-Type", value: "application/javascript" }], body: "" }
          : { requestId } }));
      return;
    }
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result);
    }
  };
  const send = (method, params = {}) => new Promise((res, rej) => {
    const n = ++id; pending.set(n, { res, rej }); ws.send(JSON.stringify({ id: n, method, params }));
  });
  const ev = async (expression) => {
    const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error("page error: " + JSON.stringify(r.exceptionDetails).slice(0, 300));
    return r.result.value;
  };
  await send("Fetch.enable", { patterns: [{ urlPattern: "qrc://*" }] });

  // ---- stub the Qt transport and load the real bridge -------------------------
  const bridgePath = join(APP_ROOT, "satellite_center", "native_desktop_bridge.js");
  const bridge = readFileSync(bridgePath, "utf8");
  const stub = `
    // Chromium never fetches an unknown scheme, so the bridge's injected
    // qwebchannel.js never fires onload; report success shortly after src is set.
    const __c = document.createElement.bind(document);
    document.createElement = function (tag, ...rest) {
      const el = __c(tag, ...rest);
      if (String(tag).toLowerCase() === "script") {
        let _s = "";
        Object.defineProperty(el, "src", { configurable: true, get: () => _s,
          set: (v) => { _s = v; if (String(v).startsWith("qrc:")) setTimeout(() => { if (typeof el.onload === "function") el.onload(); }, 100); } });
      }
      return el;
    };
    window.__calls = [];
    window.__jobs = [];
    window.qt = { webChannelTransport: { send: () => {}, onmessage: null } };
    window.QWebChannel = function (transport, cb) {
      const receiver = { reply: { connect: () => {} }, __cb: null,
        request: (rid, command, args) => {
          let a = {}; try { a = JSON.parse(args); } catch {}
          window.__calls.push({ command, args: a });
          const ok = (result) => receiver.__cb && receiver.__cb(rid, JSON.stringify({ ok: true, result }));
          if (command === "status") return ok({ jobs: window.__jobs });
          if (command === "plan_candidates") return ok({ candidates: [] });
          if (command === "clear_job") {
            window.__jobs = window.__jobs.filter((j) => j.id !== a.job_id);
            return ok({ cleared: a.job_id, remaining: window.__jobs.length });
          }
          if (command === "clear_finished_jobs") {
            window.__jobs = window.__jobs.filter((j) => j.state === "pending");
            return ok({ cleared: "finished", remaining: window.__jobs.length });
          }
          if (command === "cancel_schedule") {
            window.__jobs = window.__jobs.map((j) => j.id === a.job_id ? { ...j, state: "cancelled" } : j);
            return ok({ id: a.job_id });
          }
          return ok({});
        } };
      receiver.reply.connect = (fn) => { receiver.__cb = fn; };
      cb({ objects: { nativeReceiver: receiver } });
    };
  `;
  await ev(stub);
  await ev(bridge);
  await sleep(2500);
  check("bridge initialised in the page", await ev("Boolean(window.nativeWebBridgeReady)"),
        String(await ev("Boolean(window.nativeWebBridgeReady)")));

  const seed = async (jobs) => { await ev(`window.__jobs = ${JSON.stringify(jobs)}; 'seeded'`); };
  const refresh = async (wantCards, timeoutMs = 40000) => {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      await ev("window.dispatchEvent(new Event('tihulu-native-schedule-changed')); 'k'");
      if ((await ev("document.querySelectorAll('#jobs .scheduled-job').length")) >= wantCards) return true;
      await sleep(1500);
    }
    return false;
  };
  const state = () => ev(`JSON.stringify({
    cards: [...document.querySelectorAll('#jobs .scheduled-job h4')].map(h => h.textContent),
    buttons: [...document.querySelectorAll('#jobs button')].map(b => b.textContent.trim()),
    calendarIds: [...document.querySelectorAll('#browser-calendar-grid .sat-calendar-job')].map(c => c.dataset.jobId) })`);

  // ---- 1) controls appear, counts are right -----------------------------------
  await seed([
    { id: "a1", satellite: "ISS (ZARYA)", state: "pending", run_at: "2026-11-21T16:00:00Z", capture_seconds: 60, estimated_bytes: 126700000 },
    { id: "a2", satellite: "METEOR-M 2", state: "completed", run_at: "2026-11-21T08:00:00Z", capture_seconds: 90, estimated_bytes: 190000000 },
    { id: "a3", satellite: "METEOR-M 2", state: "cancelled", run_at: "2026-11-21T08:30:00Z", capture_seconds: 90, estimated_bytes: 190000000 },
  ]);
  check("all three records render", await refresh(3));
  const s1 = JSON.parse(await state());
  check("bulk control counts only finished records",
        s1.buttons.filter((b) => /^Clear /.test(b)).join("") === "Clear 2 finished jobs",
        s1.buttons.join("|"));
  check("every card has a dismiss control",
        s1.buttons.filter((b) => b === "×").length === 3, String(s1.buttons.filter((b) => b === "×").length));
  check("pending card also offers cancel", s1.buttons.includes("Cancel native job"));

  // ---- 2) x removes one record from list AND calendar ------------------------
  const clicked = await ev(`(() => {
    const card = [...document.querySelectorAll('#jobs .scheduled-job')].find(c => /COMPLETED/.test(c.textContent));
    const x = card && card.querySelector('button[aria-label="Remove this job from the list and calendar"]');
    if (!x) return 'none'; x.click(); return 'clicked'; })()`);
  check("x click accepted", clicked === "clicked", clicked);
  await refresh(2);
  await sleep(2500);
  const s2 = JSON.parse(await state());
  const clearCalls = await ev("JSON.stringify(window.__calls.filter(c => c.command === 'clear_job'))");
  check("x removes the record from the list", !s2.cards.join(" ").includes("COMPLETED"), s2.cards.join("|"));
  check("x removes the record from the calendar", !s2.calendarIds.includes("a2"), s2.calendarIds.join(","));
  check("x calls clear_job with the exact id", clearCalls.includes('"job_id":"a2"'), clearCalls);
  check("pending record untouched", s2.calendarIds.includes("a1"), s2.calendarIds.join(","));

  // ---- 3) bulk clear removes finished, keeps pending -------------------------
  const bulk = await ev(`(() => { const b = [...document.querySelectorAll('#jobs button')].find(b => /^Clear /.test(b.textContent)); if (!b) return 'none'; b.click(); return 'clicked'; })()`);
  check("bulk clear accepted", bulk === "clicked", bulk);
  await refresh(1);
  await sleep(2500);
  const s3 = JSON.parse(await state());
  const bulkCalls = await ev("JSON.stringify(window.__calls.filter(c => c.command === 'clear_finished_jobs'))");
  check("bulk clear removes every finished record",
        s3.cards.every((c) => /PENDING/.test(c)) && !s3.calendarIds.includes("a3"),
        s3.cards.join("|") + " / " + s3.calendarIds.join(","));
  check("bulk clear leaves pending alone", s3.calendarIds.includes("a1"), s3.calendarIds.join(","));
  check("bulk clear calls clear_finished_jobs", bulkCalls.length > 0, bulkCalls);
  check("clear control disappears when nothing finished remains",
        !s3.buttons.some((b) => /^Clear /.test(b)), s3.buttons.join("|"));

  log(`\n${failures === 0 ? "ALL CHECKS PASSED" : failures + " CHECK(S) FAILED"}`);
  return failures;
};

run()
  .then((f) => { for (const c of cleanup) { try { c.kill ? c.kill() : rmSync(c, { recursive: true, force: true }); } catch {} } process.exit(f === 0 ? 0 : 1); })
  .catch((e) => { console.error("HARNESS ERROR:", e.message); for (const c of cleanup) { try { c.kill ? c.kill() : rmSync(c, { recursive: true, force: true }); } catch {} } process.exit(2); });
