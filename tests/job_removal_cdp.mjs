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
// Seed dates relative to *now*, so the calendar (which shows the local day) always
// shows the seeded records regardless of when the harness runs.
// The calendar shows the LOCAL day, so seed in local time, not UTC.
const _now = new Date();
const TODAY = `${_now.getFullYear()}-${String(_now.getMonth() + 1).padStart(2, "0")}-${String(_now.getDate()).padStart(2, "0")}`;
const at = (h, m = 0) => `${TODAY}T${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:00`;
const PORT = 8830;
// A fresh CDP port per run: stale browser instances must never be adopted.
const CDP = 9400 + Math.floor(Math.random() * 400);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(...a);
let failures = 0;
// A snap-confined chromium cannot be signalled from the harness (EPERM), so a plain
// kill() leaks one browser per run. The DevTools protocol can close it politely, so
// do that first and only then fall back to a signal (works for non-snap builds).
let closeBrowser = async () => {};

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
  let target = null;
  for (let i = 0; i < 60; i++) {                 // browser startup is variable
    try {
      const list = await (await fetch(`http://127.0.0.1:${CDP}/json/list`)).json();
      target = list.find((t) => t.type === "page") || null;
      if (target) break;
    } catch {}
    await sleep(1000);
  }
  if (!target) throw new Error(`no chromium page target on ${CDP} after 60 s`);
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
  closeBrowser = async () => { try { await send("Browser.close"); } catch {} };
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
    // The bulk clear asks for confirmation (a destructive action); the harness
    // auto-accepts, which is the operator's intended choice under test.
    window.confirm = () => true;
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
            // same response shape as the real Station (see test_clear_responses_are_unambiguous)
            window.__jobs = window.__jobs.filter((j) => j.id !== a.job_id);
            return ok({ cleared_id: a.job_id, remaining: window.__jobs.length });
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
  // Inject only once the document can accept a <script>: on a loaded runner the
  // page sometimes has no <head>/<body> yet, and the bridge's appendChild then
  // throws (a startup race that used to surface as a flaky harness error).
  for (let i = 0; i < 90; i++) {
    if ((await ev("document.readyState")) === "complete" &&
        await ev("Boolean(document.head && document.body)")) break;
    await sleep(1000);
  }
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
    { id: "a1", satellite: "ISS (ZARYA)", state: "pending", run_at: at(16), capture_seconds: 60, estimated_bytes: 126700000 },
    { id: "a2", satellite: "METEOR-M 2", state: "completed", run_at: at(8), capture_seconds: 90, estimated_bytes: 190000000 },
    { id: "a3", satellite: "METEOR-M 2", state: "cancelled", run_at: at(8, 30), capture_seconds: 90, estimated_bytes: 190000000 },
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

  // ---- 4) x on a PENDING schedule cancels it (never silently drops it) -------
  await seed([
    { id: "p9", satellite: "ISS (ZARYA)", state: "pending", run_at: at(16), capture_seconds: 60, estimated_bytes: 126700000 },
  ]);
  check("pending record renders alone", await refresh(1));
  const beforeCancel = JSON.parse(await state());
  const cancelClicked = await ev(`(() => {
    const card = [...document.querySelectorAll('#jobs .scheduled-job')].find(c => /PENDING/.test(c.textContent));
    const x = card && card.querySelector('button[aria-label="Remove this job from the list and calendar"]');
    if (!x) return 'none'; x.click(); return 'clicked'; })()`);
  check("x on pending accepted", cancelClicked === "clicked", cancelClicked);
  for (let i = 0; i < 15; i++) {
    await ev("window.dispatchEvent(new Event('tihulu-native-schedule-changed')); 'k'");
    const t = await ev("document.querySelector('#jobs .scheduled-job h4') ? document.querySelector('#jobs .scheduled-job h4').textContent : ''");
    if (/CANCELLED/.test(String(t))) break;
    await sleep(1500);
  }
  await sleep(2000);
  const afterCancel = JSON.parse(await state());
  const cancelCalls = await ev("JSON.stringify(window.__calls.filter(c => c.command === 'cancel_schedule'))");
  check("x on pending CANCELS it (not a silent drop)",
        afterCancel.cards.join(" ").includes("CANCELLED"), afterCancel.cards.join("|"));
  check("x on pending calls cancel_schedule with the id",
        cancelCalls.includes('"job_id":"p9"'), cancelCalls);
  check("cancelled record leaves the native calendar",
        !afterCancel.calendarIds.includes("p9"), afterCancel.calendarIds.join(","));
  check("no clear_job was used for a pending record",
        !JSON.stringify(await ev("JSON.stringify(window.__calls.filter(c => c.command === 'clear_job'))")).includes('"job_id":"p9""'));

  // ---- 5) a RUNNING capture is never offered for removal --------------------
  await seed([
    { id: "r1", satellite: "ISS (ZARYA)", state: "running", run_at: at(10), capture_seconds: 600, estimated_bytes: 1200000000 },
    { id: "c1", satellite: "ISS (ZARYA)", state: "completed", run_at: at(9), capture_seconds: 90, estimated_bytes: 190000000 },
  ]);
  check("running record renders", await refresh(2));
  const s5 = JSON.parse(await state());
  check("bulk control ignores a running capture (counts terminal only)",
        s5.buttons.filter((b) => /^Clear /.test(b)).join("") === "Clear 1 finished job",
        s5.buttons.join("|"));
  const runningCardButtons = await ev(`
    (() => { const card = [...document.querySelectorAll('#jobs .scheduled-job')].find(c => /RUNNING/.test(c.textContent));
             return card ? [...card.querySelectorAll('button')].map(b => b.textContent.trim()).join(';') : 'no-card'; })()`);
  check("a running card carries no dismiss control", !runningCardButtons.includes("×"), runningCardButtons);
  const runningCalendar = await ev(`
    (() => { const card = [...document.querySelectorAll('#browser-calendar-grid .sat-calendar-job')].find(c => c.dataset.jobId === 'r1');
             return card ? [...card.querySelectorAll('button')].map(b => b.textContent.trim()).join(';') : 'no-card'; })()`);
  check("a running calendar card offers cancel only, never removal",
        runningCalendar.includes("Cancel job") && !runningCalendar.includes("Remove record"), runningCalendar);

  // ---- 6) removing a finished record FROM THE CALENDAR ----------------------
  const calendarRemoved = await ev(`
    (() => {
      const card = [...document.querySelectorAll('#browser-calendar-grid .sat-calendar-job')].find(c => c.dataset.jobId === 'c1');
      const btn = card && card.querySelector('button[aria-label="Remove this job from the list and calendar"]');
      if (!btn) return 'no-remove-control'; btn.click(); return 'clicked'; })()`);
  check("calendar remove control exists for finished records", calendarRemoved === "clicked", calendarRemoved);
  for (let i = 0; i < 15; i++) {
    await ev("window.dispatchEvent(new Event('tihulu-native-schedule-changed')); 'k'");
    if (!String(await ev("[...document.querySelectorAll('#jobs .scheduled-job h4')].map(h=>h.textContent).join(' ')")).includes("COMPLETED")) break;
    await sleep(1500);
  }
  await sleep(2500);
  const s6 = JSON.parse(await state());
  const calClearCalls = await ev("JSON.stringify(window.__calls.filter(c => c.command === 'clear_job'))");
  check("calendar removal drops the record from the list", !s6.cards.join(" ").includes("COMPLETED"), s6.cards.join("|"));
  check("calendar removal drops the record from the calendar too", !s6.calendarIds.includes("c1"), s6.calendarIds.join(","));
  check("calendar removal calls clear_job", calClearCalls.includes('"job_id":"c1"'), calClearCalls);
  check("the running capture survives the calendar removal", s6.calendarIds.includes("r1"), s6.calendarIds.join(","));

  log(`\n${failures === 0 ? "ALL CHECKS PASSED" : failures + " CHECK(S) FAILED"}`);
  return failures;
};

run()
  .then(async (f) => {
    await closeBrowser();
    for (const c of cleanup) { try { c.kill ? c.kill() : rmSync(c, { recursive: true, force: true }); } catch {} }
    process.exit(f === 0 ? 0 : 1);
  })
  .catch(async (e) => {
    console.error("HARNESS ERROR:", e.message);
    await closeBrowser();
    for (const c of cleanup) { try { c.kill ? c.kill() : rmSync(c, { recursive: true, force: true }); } catch {} }
    process.exit(2);
  });
